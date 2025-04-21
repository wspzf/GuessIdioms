#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
看图猜成语插件 - 提供群聊看图猜成语游戏功能
"""
import os
import re
import time
import aiohttp
import tomllib
import asyncio
import random
import sqlite3
from typing import Dict, List, Set, Tuple, Optional, Any
from dataclasses import dataclass, field
from datetime import datetime
import logging
from loguru import logger

# 导入插件基类和工具
from utils.plugin_base import PluginBase
from utils.decorators import *
from WechatAPI import WechatAPIClient

# 尝试导入积分管理插件
try:
    from plugins.AdminPoint.main import AdminPoint
except ImportError:
    AdminPoint = None

# 尝试导入昵称同步插件
try:
    from plugins.NicknameSync.main import NicknameDatabase
except ImportError:
    NicknameDatabase = None

@dataclass
class GameRound:
    """单轮游戏数据"""
    image_url: str  # 成语图片URL
    idiom: Optional[str] = None  # 正确成语
    hint_chars: List[str] = field(default_factory=list)  # 已提示的字符
    start_time: float = field(default_factory=time.time)  # 开始时间
    correct_user: Optional[str] = None  # 答对的用户ID
    is_completed: bool = False  # 是否已完成
    hint_count: int = 0  # 已提示次数

@dataclass
class GameSession:
    """游戏会话数据"""
    chatroom_id: str  # 群聊ID
    rounds: List[GameRound] = field(default_factory=list)  # 游戏轮次
    current_round: int = 0  # 当前轮次
    total_rounds: int = 5  # 总轮次
    active: bool = True  # 游戏是否进行中
    players: Dict[str, int] = field(default_factory=dict)  # 玩家得分 {wxid: score}
    start_time: float = field(default_factory=time.time)  # 游戏开始时间

class GuessIdioms(PluginBase):
    """看图猜成语插件，提供群聊成语猜谜游戏"""
    
    description = "看图猜成语插件 - 根据图片猜测对应的成语"
    author = "wspzf"
    version = "1.0.0"
    
    def __init__(self):
        super().__init__()
        
        # 获取配置文件路径
        config_path = os.path.join(os.path.dirname(__file__), "config.toml")
        
        try:
            with open(config_path, "rb") as f:
                config = tomllib.load(f)
                
            # 读取基本配置
            game_config = config.get("GuessIdioms", {})
            self.enable = game_config.get("enable", True)
            self.commands = game_config.get("commands", ["看图猜成语", "成语猜猜", "猜成语"])
            self.command_tip = game_config.get("command-tip", "")
            
            # 游戏设置
            self.rounds_per_game = game_config.get("rounds-per-game", 5)
            self.initial_wait_time = game_config.get("initial-wait-time", 10)
            self.hint_interval = game_config.get("hint-interval", 15)
            self.max_hints = game_config.get("max-hints", 2)
            self.round_timeout = game_config.get("round-timeout", 60)  # 每轮最长时间
            
            # API设置
            self.api_url = game_config.get("api-url", "https://xiaoapi.cn/API/game_ktccy.php")
            self.token = game_config.get("token", "")
            
            # 调试设置
            self.debug_mode = game_config.get("debug-mode", False)
            
            # 设置日志级别
            if self.debug_mode:
                logger.level("DEBUG")
                logger.debug("调试模式已启用")
            
            # 积分设置
            self.base_points = game_config.get("base-points", 10)
            self.bonus_points = game_config.get("bonus-points", [5, 3, 1])
            
            logger.info(f"看图猜成语插件初始化成功，API地址: {self.api_url}")
            
        except Exception as e:
            logger.error(f"加载GuessIdioms配置文件失败: {str(e)}")
            # 设置默认值
            self.enable = True
            self.commands = ["看图猜成语", "成语猜猜", "猜成语"]
            self.command_tip = ""
            self.rounds_per_game = 5
            self.initial_wait_time = 10
            self.hint_interval = 15
            self.max_hints = 2
            self.round_timeout = 60
            self.api_url = "https://xiaoapi.cn/API/game_ktccy.php"
            self.token = ""
            self.debug_mode = False
            self.base_points = 10
            self.bonus_points = [5, 3, 1]
            
            # 创建默认配置文件
            self._create_default_config(config_path)
        
        # 游戏会话
        self.game_sessions: Dict[str, GameSession] = {}
        
        # 昵称数据库
        self.nickname_db = None
        
        # 积分管理插件
        self.admin_point = None
        
        # 机器人实例
        self.bot = None
        
        # 初始化玩家昵称字典
        self.player_nicknames = {}
    
    def _create_default_config(self, config_path: str):
        """创建默认配置文件"""
        try:
            os.makedirs(os.path.dirname(config_path), exist_ok=True)
            
            default_config = """[GuessIdioms]
# 插件基本设置
enable = true
commands = ["看图猜成语", "成语猜猜", "猜成语"]
command-tip = "发送\\"看图猜成语\\"开始游戏"

# 游戏设置
rounds-per-game = 5  # 每局游戏轮数
initial-wait-time = 10  # 开始等待时间(秒)
hint-interval = 15  # 提示间隔时间(秒)
max-hints = 2  # 最大提示次数
round-timeout = 60  # 每轮最长时间(秒)

# API设置
api-url = "https://xiaoapi.cn/API/game_ktccy.php"
token = ""  # API令牌(如需要)

# 调试设置
debug-mode = false  # 调试模式

# 积分设置
base-points = 10  # 每题基础积分
bonus-points = [5, 3, 1]  # 排名奖励积分
"""
            
            with open(config_path, "w", encoding="utf-8") as f:
                f.write(default_config)
                
            logger.info(f"已创建默认配置文件: {config_path}")
        except Exception as e:
            logger.error(f"创建默认配置文件失败: {str(e)}")
    
    async def async_init(self):
        """异步初始化"""
        # 初始化昵称数据库
        if NicknameDatabase:
            try:
                # 适配不同环境的路径
                if os.path.exists("/app/database"):
                    db_path = "/app/database/nickname.db"
                else:
                    db_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 
                                          "database", "nickname.db")
                
                self.nickname_db = NicknameDatabase(db_path)
                logger.info(f"昵称数据库初始化成功: {db_path}")
            except Exception as e:
                logger.error(f"初始化昵称数据库失败: {str(e)}")
                self.nickname_db = None
    
    def get_nickname(self, wxid: str, chatroom_id: str = None) -> str:
        """获取用户昵称
        
        Args:
            wxid: 用户wxid
            chatroom_id: 群聊ID，用于匹配对应的房间号
            
        Returns:
            str: 用户昵称
        """
        if not wxid:
            return "未知用户"
            
        try:
            # 从pluginsDB.db数据库中读取昵称
            db_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 
                                  "database", "pluginsDB.db")
            
            # 如果在Docker环境中
            if os.path.exists("/app/database"):
                db_path = "/app/database/pluginsDB.db"
                
            if not os.path.exists(db_path):
                logger.warning(f"昵称数据库不存在: {db_path}")
                return self._get_fallback_nickname(wxid)
                
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()
            
            # 优先查询指定群聊中的昵称
            if chatroom_id:
                cursor.execute("""
                SELECT nickname FROM nickname 
                WHERE wxid = ? AND chatroom_id = ? AND is_group = 1
                ORDER BY update_time DESC LIMIT 1
                """, (wxid, chatroom_id))
                
                result = cursor.fetchone()
                if result:
                    conn.close()
                    return result[0]
            
            # 如果没有找到群聊昵称，查询任意场景下的昵称
            cursor.execute("""
            SELECT nickname FROM nickname 
            WHERE wxid = ?
            ORDER BY update_time DESC LIMIT 1
            """, (wxid,))
            
            result = cursor.fetchone()
            conn.close()
            
            if result:
                return result[0]
            else:
                return self._get_fallback_nickname(wxid)
                
        except Exception as e:
            logger.error(f"从数据库获取昵称失败: {str(e)}")
            return self._get_fallback_nickname(wxid)
    
    def _get_fallback_nickname(self, wxid: str) -> str:
        """获取备用昵称（当数据库查询失败时使用）"""
        # 使用玩家序号作为备用昵称
        player_index = len(self.player_nicknames) + 1 if hasattr(self, 'player_nicknames') else 1
        if not hasattr(self, 'player_nicknames'):
            self.player_nicknames = {}
        if wxid not in self.player_nicknames:
            self.player_nicknames[wxid] = f"玩家{player_index}"
        return self.player_nicknames[wxid]
    
    async def add_points(self, wxid: str, points: int) -> bool:
        """添加积分"""
        try:
            # 直接使用XYBotDB实例，不依赖插件系统
            from database.XYBotDB import XYBotDB
            db = XYBotDB()
            success = db.add_points(wxid, points)
            if success:
                logger.info(f"为用户 {wxid} 添加了 {points} 积分")
                return True
            else:
                logger.error(f"添加积分失败: 数据库操作未成功")
                return False
        except Exception as e:
            logger.error(f"添加积分失败: {str(e)}")
            return False
    
    async def start_game(self, bot: WechatAPIClient, chatroom_id: str):
        """开始一局新游戏"""
        logger.info(f"尝试开始游戏，群ID：{chatroom_id}")
        
        if chatroom_id in self.game_sessions and self.game_sessions[chatroom_id].active:
            await bot.send_text_message(chatroom_id, "已经有一局游戏正在进行中，请等待当前游戏结束。")
            return
        
        # 创建新游戏会话
        session = GameSession(
            chatroom_id=chatroom_id,
            total_rounds=self.rounds_per_game
        )
        self.game_sessions[chatroom_id] = session
        
        await bot.send_text_message(chatroom_id, f"🎮 看图猜成语游戏开始！\n本局游戏共{self.rounds_per_game}轮，猜对一题得{self.base_points}分！")
        
        # 启动游戏循环
        task = asyncio.create_task(self.game_loop(bot, chatroom_id))
        # 添加异常处理
        task.add_done_callback(lambda t: self.handle_game_exception(t, chatroom_id, bot))
    
    def handle_game_exception(self, task, chatroom_id, bot):
        """处理游戏任务异常"""
        try:
            # 获取任务异常(如果有)
            exc = task.exception()
            if exc:
                logger.error(f"游戏任务出错: {str(exc)}")
                # 尝试发送错误消息给群聊
                asyncio.create_task(bot.send_text_message(chatroom_id, f"游戏运行出错，请稍后再试: {str(exc)}"))
                # 清理游戏会话
                if chatroom_id in self.game_sessions:
                    self.game_sessions[chatroom_id].active = False
        except Exception as e:
            logger.error(f"处理游戏异常时出错: {str(e)}")
    
    async def game_loop(self, bot: WechatAPIClient, chatroom_id: str):
        """游戏主循环"""
        session = self.game_sessions.get(chatroom_id)
        if not session:
            return
            
        # 连续无人答对的轮数
        unanswered_rounds = 0
        
        while session.current_round < session.total_rounds and session.active:
            # 获取成语和图片
            success, msg, image_url, answer = await self.fetch_game_data(chatroom_id)
            if not success or not image_url:
                await bot.send_text_message(chatroom_id, f"❌ 获取成语失败: {msg}")
                session.active = False
                break
                
            # 创建新一轮
            game_round = GameRound(
                image_url=image_url,
                idiom=answer  # 保存正确答案
            )
            session.rounds.append(game_round)
            
            # 发送图片和提示
            try:
                await bot.send_text_message(chatroom_id, f"🔍 第 {session.current_round + 1}/{session.total_rounds} 轮")
                
                # 从URL下载图片数据并发送
                image_data = await self._download_image(image_url)
                if image_data:
                    # 发送图片数据
                    await bot.send_image_message(chatroom_id, image_data)
                else:
                    # 下载失败时提示错误并跳过本轮
                    await bot.send_text_message(chatroom_id, f"❌ 获取图片失败，跳过本轮游戏。")
                    session.current_round += 1
                    continue
                    
                await bot.send_text_message(chatroom_id, "⏱️ 请猜出图片代表的成语...")
            except Exception as e:
                logger.error(f"发送图片失败: {str(e)}")
                await bot.send_text_message(chatroom_id, f"❌ 发送图片失败，跳过当前轮次。")
                session.current_round += 1
                continue
                
            # 启动提示计时器
            hint_task = asyncio.create_task(self.hint_timer(bot, chatroom_id, session.current_round))
            
            # 设置轮次超时
            timeout_task = asyncio.create_task(self.round_timeout_timer(bot, chatroom_id, session.current_round))
            
            # 等待本轮结束
            while not game_round.is_completed and session.active:
                await asyncio.sleep(1)
            
            # 取消提示和超时任务
            hint_task.cancel()
            timeout_task.cancel()
            
            # 如果游戏已不再激活（被手动结束），立即退出轮次循环
            if not session.active:
                break
            
            # 如果本轮已完成，显示结果
            if game_round.is_completed and game_round.correct_user:
                nickname = self.get_nickname(game_round.correct_user, chatroom_id)
                # 使用send_at_message正确实现@功能
                await bot.send_at_message(chatroom_id, 
                                f"🎉 恭喜猜对了！\n正确答案是: {game_round.idiom}", 
                                [game_round.correct_user])
                
                # 增加得分
                session.players[game_round.correct_user] = session.players.get(game_round.correct_user, 0) + 1
                # 重置无人答对计数
                unanswered_rounds = 0
            else:
                # 显示正确答案
                await bot.send_text_message(chatroom_id, f"⏱️ 时间到！没有人猜对。\n正确答案是: {game_round.idiom}")
                # 增加无人答对计数
                unanswered_rounds += 1
                
                # 如果连续两轮无人答对，结束游戏
                if unanswered_rounds >= 2:
                    # 判断是否是最后一轮游戏，如果是最后一轮则不显示提示，直接结束
                    if session.current_round + 1 >= session.total_rounds:
                        # 最后一轮游戏，不显示提示，直接结束
                        logger.info(f"连续两轮无人答对，且已是最后一轮，游戏正常结束")
                    else:
                        # 不是最后一轮，显示提示
                        await bot.send_text_message(chatroom_id, "🔔 连续两轮无人答对，游戏自动结束！")
                    break
            
            # 进入下一轮
            session.current_round += 1
            
            # 轮次间隔
            await asyncio.sleep(3)
            
        # 游戏结束，显示结果
        if session.active:  # 只有在正常结束时结算
            await self.end_game(bot, chatroom_id)
        
        # 清理会话
        if chatroom_id in self.game_sessions:
            del self.game_sessions[chatroom_id]
    
    async def _download_image(self, url: str) -> Optional[bytes]:
        """下载图片内容"""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as response:
                    if response.status != 200:
                        logger.error(f"下载图片失败: 状态码 {response.status}")
                        return None
                    return await response.read()
        except Exception as e:
            logger.error(f"下载图片失败: {str(e)}")
            return None
    
    async def hint_timer(self, bot: WechatAPIClient, chatroom_id: str, round_idx: int):
        """提示计时器"""
        try:
            session = self.game_sessions.get(chatroom_id)
            if not session or round_idx >= len(session.rounds):
                return
                
            game_round = session.rounds[round_idx]
            
            # 初始等待
            await asyncio.sleep(self.initial_wait_time)
            
            # 提供提示，最多提供max_hints次
            for i in range(self.max_hints):
                if game_round.is_completed or not session.active:
                    break
                
                # 生成提示
                if game_round.idiom:
                    # 已知答案，直接生成提示
                    hint = await self.generate_hint(game_round.idiom, game_round.hint_chars)
                    game_round.hint_count += 1
                    
                    if hint:
                        await bot.send_text_message(chatroom_id, f"💡 提示 {i+1}/{self.max_hints}: {hint}")
                    else:
                        await bot.send_text_message(chatroom_id, f"💡 提示 {i+1}/{self.max_hints}: 暂无更多提示")
                else:
                    await bot.send_text_message(chatroom_id, f"❓ 无法生成提示")
                
                # 等待下一次提示
                if i < self.max_hints - 1:
                    await asyncio.sleep(self.hint_interval)
                
        except asyncio.CancelledError:
            # 提示任务被取消，正常退出
            pass
        except Exception as e:
            logger.error(f"提示计时器错误: {str(e)}")
    
    async def round_timeout_timer(self, bot: WechatAPIClient, chatroom_id: str, round_idx: int):
        """轮次超时计时器"""
        try:
            session = self.game_sessions.get(chatroom_id)
            if not session or round_idx >= len(session.rounds):
                return
                
            game_round = session.rounds[round_idx]
            
            # 等待超时时间
            await asyncio.sleep(self.round_timeout)
            
            # 如果还未完成，标记为完成
            if not game_round.is_completed and session.active:
                game_round.is_completed = True
                
        except asyncio.CancelledError:
            # 任务被取消，正常退出
            pass
        except Exception as e:
            logger.error(f"轮次超时计时器错误: {str(e)}")
    
    async def generate_hint(self, idiom: str, existing_hints: List[str]) -> str:
        """生成提示"""
        if not idiom:
            return ""
            
        # 如果没有已提示的字符，随机选择一个
        if not existing_hints:
            hint_char = random.choice(idiom)
            existing_hints.append(hint_char)
            
            # 构建提示字符串，只显示提示字符，其他用❓代替
            hint_text = ""
            for c in idiom:
                if c == hint_char:
                    hint_text += c
                else:
                    hint_text += "❓"
            return f"提示: {hint_text}"
            
        # 如果已有提示，选择一个未提示的字符
        available_chars = [c for c in idiom if c not in existing_hints]
        if not available_chars:
            return ""
            
        hint_char = random.choice(available_chars)
        existing_hints.append(hint_char)
        
        # 构建提示字符串
        hint_text = ""
        for c in idiom:
            if c in existing_hints:
                hint_text += c
            else:
                hint_text += "❓"
        return f"提示: {hint_text}"
    
    async def check_answer(self, chatroom_id: str, user_guess: str, correct_answer: str) -> bool:
        """检查答案是否正确"""
        # 直接比较用户猜测和正确答案
        return user_guess == correct_answer
    
    async def end_game(self, bot: WechatAPIClient, chatroom_id: str):
        """结束游戏并结算积分"""
        session = self.game_sessions.get(chatroom_id)
        if not session:
            return
            
        # 计算排名
        rankings = sorted(session.players.items(), key=lambda x: x[1], reverse=True)
        
        # 构建结果消息
        result_msg = "🏆 看图猜成语游戏结束！\n\n📊 最终排名:\n"
        
        # 处理无参与者的情况
        if not rankings:
            result_msg += "本局游戏无人答对！\n"
        else:
            # 准备被@的用户列表
            at_list = []
            
            for i, (wxid, score) in enumerate(rankings):
                nickname = self.get_nickname(wxid, chatroom_id)
                result_msg += f"{i+1}. {nickname}: {score}题\n"
                at_list.append(wxid)  # 添加到@列表
                
                # 计算积分奖励
                points = score * self.base_points  # 基础积分
                
                # 排名奖励
                if i < len(self.bonus_points):
                    points += self.bonus_points[i]
                    
                # 发放积分
                if points > 0:
                    success = await self.add_points(wxid, points)
                    if success:
                        result_msg += f"   🎁 奖励{points}积分\n"
            
            # 发送结果，同时@所有参与者
            if at_list:
                await bot.send_at_message(chatroom_id, result_msg, at_list)
            else:
                await bot.send_text_message(chatroom_id, result_msg)
    
    async def fetch_game_data(self, chatroom_id: str) -> Tuple[bool, str, Optional[str], Optional[str]]:
        """从API获取游戏数据
        
        Args:
            chatroom_id: 群聊ID，用作游戏的唯一标识
            
        Returns:
            (成功标志, 消息文本, 图片URL, 答案)
        """
        # 移除@chatroom后缀
        room_id = chatroom_id.replace("@chatroom", "")
        
        # 构建API请求URL
        from urllib.parse import quote
        encoded_id = quote(str(room_id))
        full_url = f"{self.api_url}?msg=开始游戏&id={encoded_id}"
        
        logger.info(f"API请求: {full_url}")
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(full_url) as response:
                    if response.status == 200:
                        try:
                            response_text = await response.text()
                            logger.debug(f"API原始响应: {response_text}")
                            
                            # 尝试解析JSON
                            try:
                                data = await response.json(content_type=None)
                                logger.debug(f"API解析后数据: {data}")
                                
                                # 检查返回状态
                                if "code" in data and data["code"] == 200:
                                    # API返回格式为 {code:200, data:{msg:"消息", pic:"图片URL"}, answer:"答案"}
                                    msg = data.get("data", {}).get("msg", "")
                                    pic_url = data.get("data", {}).get("pic", "")
                                    
                                    # 优先从data字段获取answer
                                    answer = data.get("data", {}).get("answer", "")
                                    
                                    # 如果data中没有answer，尝试从根级别获取
                                    if not answer:
                                        answer = data.get("answer", "")
                                    
                                    logger.info(f"成功解析API返回: msg={msg}, pic_url={pic_url}, answer={answer}")
                                    
                                    # 如果仍然没有答案，尝试从消息中提取
                                    if not answer and ("答案" in msg):
                                        idiom_match = re.search(r'答案[是为：:\s]+[""]?([^""\s，。,\.]+)[""]?', msg)
                                        if idiom_match:
                                            answer = idiom_match.group(1)
                                            logger.info(f"从返回消息中提取答案: {answer}")
                                    
                                    return True, msg, pic_url, answer
                                else:
                                    err_msg = f"API返回错误码: {data.get('code')}, 消息: {data.get('msg', '')}"
                                    logger.error(err_msg)
                                    return False, err_msg, None, None
                            except Exception as e:
                                # 如果JSON解析失败，尝试手动解析
                                logger.warning(f"JSON解析失败: {str(e)}，尝试手动处理响应")
                                
                                # 检查是否包含图片URL
                                pic_match = re.search(r'http[s]?://(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*\\(\\),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+\.(?:jpg|jpeg|png|gif)', response_text)
                                answer_match = re.search(r'答案[是为：:\s]+[""]?([^""\s，。,\.]+)[""]?', response_text)
                                
                                if pic_match:
                                    pic_url = pic_match.group(0)
                                    answer = answer_match.group(1) if answer_match else ""
                                    return True, "获取图片成功", pic_url, answer
                                return False, "无法解析API响应", None, None
                        except Exception as e:
                            err_msg = f"解析API响应时出错: {str(e)}"
                            logger.error(err_msg)
                            return False, err_msg, None, None
                    else:
                        err_msg = f"API请求失败，状态码: {response.status}"
                        logger.error(err_msg)
                        return False, err_msg, None, None
        except Exception as e:
            err_msg = f"调用API异常: {str(e)}"
            logger.error(err_msg)
            return False, err_msg, None, None
    
    @on_text_message(priority=50)
    async def handle_text(self, bot: WechatAPIClient, message: dict):
        """处理文本消息"""
        if not self.enable:
            return
            
        self.bot = bot
        content = message.get("Content", "")
        sender_wxid = message.get("SenderWxid", "")
        from_wxid = message.get("FromWxid", "")
        
        # 处理游戏命令
        if content in self.commands:
            logger.info(f"收到游戏命令: {content}，发送者: {sender_wxid}, 群ID: {from_wxid}")
            if from_wxid.endswith("@chatroom"):
                await self.start_game(bot, from_wxid)
            else:
                await bot.send_text_message(sender_wxid, "看图猜成语游戏仅支持在群聊中使用。")
            return
            
        # 处理结束游戏命令
        if content == "结束游戏" and from_wxid.endswith("@chatroom") and from_wxid in self.game_sessions:
            logger.info(f"收到结束游戏命令，群ID: {from_wxid}, 发送者: {sender_wxid}")
            session = self.game_sessions[from_wxid]
            if session.active:
                # 先标记游戏为非活动状态，防止其他消息处理
                session.active = False
                
                # 如果当前轮次未完成，显示答案
                if session.current_round < len(session.rounds):
                    game_round = session.rounds[session.current_round]
                    if not game_round.is_completed and game_round.idiom:
                        await bot.send_text_message(from_wxid, f"🛑 游戏已手动结束！\n当前题目答案是: {game_round.idiom}")
                        
                # 结算游戏
                await self.end_game(bot, from_wxid)
                
                # 清理会话
                if from_wxid in self.game_sessions:
                    del self.game_sessions[from_wxid]
                
            return
            
        # 处理游戏中的答案
        if from_wxid.endswith("@chatroom") and from_wxid in self.game_sessions:
            session = self.game_sessions[from_wxid]
            if session.active and session.current_round < len(session.rounds):
                game_round = session.rounds[session.current_round]
                
                # 如果还未完成，检查答案是否正确
                if not game_round.is_completed:
                    is_correct = await self.check_answer(from_wxid, content, game_round.idiom)
                    if is_correct:
                        game_round.correct_user = sender_wxid
                        game_round.is_completed = True 