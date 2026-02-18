import asyncio
import random
import json
import os
from collections import defaultdict
from typing import Set, Dict, List, Optional
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api import logger
import astrbot.api.message_components as Comp


@register("群老婆", "author", "支持分群固定配对和随机配对的双模式今日老婆插件", "2.1.0", "repo url")
class MyPlugin(Star):
    def __init__(self, context: Context, config: Optional[Dict] = None): 
        super().__init__(context)
        
        # 重要：必须接收 config 参数
        self.plugin_config = config or {}
        
        # 如果配置为空，尝试从文件读取
        if not self.plugin_config:
            self.plugin_config = self._load_config_from_file()
        
        # 调试信息
        logger.info(f"插件配置键: {list(self.plugin_config.keys())}")
        logger.info(f"fixed_pairings 值: {repr(self.plugin_config.get('fixed_pairings', ''))}")
        
        # 核心数据结构
        self.active_users: Dict[str, Set[str]] = {}
        self.daily_marriages: Dict[str, Dict[str, str]] = {}
        # 固定配对改为按群存储: {群号: {用户QQ: 老婆QQ}}
        self.fixed_pairings: Dict[str, Dict[str, str]] = {}
        self.locks = defaultdict(asyncio.Lock)
        
        # 从配置加载固定配对
        self._load_fixed_pairings()
        
        # 定时任务
        self.scheduler = AsyncIOScheduler()
        reset_hour = self.plugin_config.get("reset_hour", 4)
        self.scheduler.add_job(self.reset_daily_data, 'cron', hour=reset_hour, minute=0)
        self.scheduler.start()
        
        fixed_count = sum(len(p) for p in self.fixed_pairings.values())
        logger.info(f"今日老婆插件已启动，每天 {reset_hour}:00 重置数据。固定配对数: {fixed_count}")

    def _load_config_from_file(self) -> Dict:
        """直接从配置文件读取"""
        # 先尝试直接读取已知路径
        known_path = "/AstrBot/data/config/astrbot_plugin_today_wife_config.json"
        
        if os.path.exists(known_path):
            try:
                with open(known_path, 'r', encoding='utf-8') as f:
                    config = json.load(f)
                    logger.info(f"从文件 {known_path} 读取配置成功")
                    return config
            except Exception as e:
                logger.error(f"读取配置文件失败: {e}")
                return {"reset_hour": 4, "fixed_pairings": "", "enable_random_pairing": True, "not_in_group_text": "你绑定的老婆今天不在这个群里哦~"}
        
        # 如果已知路径不存在，尝试其他可能路径
        config_paths = [
            "/AstrBot/data/config/群老婆_config.json",
            os.path.join(os.path.dirname(__file__), "config.json"),
        ]
        
        for config_path in config_paths:
            if os.path.exists(config_path):
                try:
                    with open(config_path, 'r', encoding='utf-8') as f:
                        config = json.load(f)
                        logger.info(f"从备用文件 {config_path} 读取配置成功")
                        return config
                except Exception as e:
                    logger.error(f"读取备用配置文件失败: {e}")
        
        logger.warning("未找到插件配置文件，使用默认配置")
        return {"reset_hour": 4, "fixed_pairings": "", "enable_random_pairing": True, "not_in_group_text": "你绑定的老婆今天不在这个群里哦~"}

    def _load_fixed_pairings(self):
        """从配置文件加载固定配对（支持分群配置）
        
        配置格式（每行一个配对）：
        群号|用户QQ|老婆QQ|提示文本(可选)
        
        示例：
        123456|111111|222222
        789012|333333|444444|你的老婆不在这个群哦
        """
        fixed_config = self.plugin_config.get("fixed_pairings", "").strip()
        
        logger.info(f"处理 fixed_pairings: {repr(fixed_config)}")
        
        if not fixed_config:
            logger.info("未配置固定配对，将使用随机配对模式")
            return
        
        # 解析配置行
        lines = fixed_config.split('\n')
        for line in lines:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            
            parts = line.split('|')
            if len(parts) < 3:
                logger.warning(f"配置格式错误，跳过: {line}")
                continue
            
            group_id = parts[0].strip()
            user_qq = parts[1].strip()
            wife_qq = parts[2].strip()
            # 可选的提示文本（当老婆不在群里时显示）
            not_in_group_text = parts[3].strip() if len(parts) > 3 else None
            
            if not group_id or not user_qq or not wife_qq:
                continue
            
            # 初始化该群的配对字典
            if group_id not in self.fixed_pairings:
                self.fixed_pairings[group_id] = {}
            
            # 存储配对信息
            self.fixed_pairings[group_id][user_qq] = {
                "wife": wife_qq,
                "text": not_in_group_text
            }
            logger.info(f"加载固定配对: 群{group_id} - {user_qq} <-> {wife_qq}")
        
        total = sum(len(p) for p in self.fixed_pairings.values())
        logger.info(f"成功加载 {len(self.fixed_pairings)} 个群的 {total} 对固定配对")

    def get_fixed_pairing(self, group_id: str, user_id: str) -> Optional[Dict]:
        """获取用户的固定配对（如果有）
        
        返回: {"wife": 老婆QQ, "text": 不在群时的提示文本} 或 None
        """
        if group_id in self.fixed_pairings:
            return self.fixed_pairings[group_id].get(user_id)
        return None

    async def reset_daily_data(self):
        """每日重置：清除非固定配对的每日数据"""
        self.active_users.clear()
        
        # 只清除非固定配对的每日婚姻
        for group_id in list(self.daily_marriages.keys()):
            self.daily_marriages[group_id].clear()
            
            # 如果有固定配对，重新应用
            if group_id in self.fixed_pairings:
                for user_id, info in self.fixed_pairings[group_id].items():
                    wife_id = info["wife"]
                    self.daily_marriages[group_id][user_id] = wife_id
        
        logger.info("每日发言记录已清空，固定配对已保留。")

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def handle_all_messages(self, event: AstrMessageEvent):
        """记录发言用户（仅用于随机配对模式）"""
        if not event.message_str or event.message_str.startswith("/"):
            return

        group_id = event.get_group_id()
        user_id = event.get_sender_id()
        if not group_id: 
            return

        # 如果有固定配对，跳过发言记录
        if self.get_fixed_pairing(group_id, user_id):
            return

        async with self.locks[group_id]:
            if group_id not in self.active_users:
                self.active_users[group_id] = set()

            if user_id not in self.active_users[group_id]:
                self.active_users[group_id].add(user_id)

    async def _get_group_members(self, event: AstrMessageEvent, group_id: str) -> Set[str]:
        """获取群成员列表"""
        try:
            # 使用 AstrBot 的 API 获取群成员
            group = await event.get_group(group_id)
            if group and group.members:
                return set(str(m.user_id) for m in group.members)
            return set()
        except Exception as e:
            logger.warning(f"获取群成员失败: {e}")
            return set()

    @filter.command("今日老婆")
    async def marry_me(self, event: AstrMessageEvent):
        """核心指令：优先使用固定配对，否则使用随机配对"""
        group_id = event.get_group_id()
        user_id = event.get_sender_id()
        
        if not group_id:
            yield event.plain_result("此功能仅限群聊使用。")
            return

        async with self.locks[group_id]:
            # 初始化数据结构
            if group_id not in self.daily_marriages:
                self.daily_marriages[group_id] = {}
            
            # 首先检查固定配对
            fixed_info = self.get_fixed_pairing(group_id, user_id)
            if fixed_info:
                fixed_wife = fixed_info["wife"]
                not_in_group_text = fixed_info.get("text")
                
                # 获取当前群成员，检查老婆是否在群里
                group_members = await self._get_group_members(event, group_id)
                wife_in_group = fixed_wife in group_members if group_members else False
                
                if not wife_in_group:
                    # 老婆不在群里，显示自定义文本或默认文本
                    if not_in_group_text:
                        yield event.plain_result(not_in_group_text)
                    else:
                        default_text = self.plugin_config.get("not_in_group_text", "你绑定的老婆今天不在这个群里哦~")
                        yield event.plain_result(default_text)
                    return
                
                # 老婆在群里，正常显示
                # 确保固定配对已经注册到每日婚姻中
                if user_id not in self.daily_marriages[group_id]:
                    self.daily_marriages[group_id][user_id] = fixed_wife
                
                yield self.build_marriage_result(event, user_id, fixed_wife, is_fixed=True, wife_in_group=True)
                return
            
            # 如果没有固定配对，检查是否已经有每日老婆
            married_dict = self.daily_marriages[group_id]
            if user_id in married_dict:
                wife_id = married_dict[user_id]
                yield self.build_marriage_result(event, user_id, wife_id, is_new=False, wife_in_group=True)
                return
            
            # 检查是否启用随机配对
            enable_random = self.plugin_config.get("enable_random_pairing", True)
            if not enable_random:
                yield event.plain_result("随机配对功能已关闭，且你未在固定配对列表中。")
                return
            
            # 确保用户记录在活跃列表中
            if group_id not in self.active_users:
                self.active_users[group_id] = set()
            self.active_users[group_id].add(user_id)
            
            # 准备随机候选人
            active_members = self.active_users[group_id]
            married_people = set(married_dict.keys())
            candidates = [
                uid for uid in active_members 
                if uid not in married_people and uid != user_id
            ]

            if not candidates:
                yield event.plain_result("没有落单的群友了，大家都已经结为连理了...")
                return

            selected_wife = random.choice(candidates)
            married_dict[user_id] = selected_wife
            married_dict[selected_wife] = user_id
            
            logger.info(f"群 {group_id} 随机配对成功: {user_id} & {selected_wife}")
            yield self.build_marriage_result(event, user_id, selected_wife, is_new=True, wife_in_group=True)

    def build_marriage_result(self, event, user_id, wife_id, is_new=True, is_fixed=False, wife_in_group=True):
        """构建结果消息链
        
        Args:
            wife_in_group: 老婆是否在群里（不在时不能@，否则会报错）
        """
        avatar_url = f"https://q.qlogo.cn/headimg_dl?dst_uin={wife_id}&spec=640"
        
        if is_fixed:
            msg_text = " 💝 命中注定！你的永恒老婆是："
        elif is_new:
            msg_text = " ✨ 恭喜！你今天的命定老婆是："
        else:
            msg_text = " ❤️ 别贪心，你今天的法定老婆依然是："
        
        chain = [Comp.At(qq=user_id), Comp.Plain(msg_text)]
        
        # 只有在群里才 @ 老婆，否则只显示 QQ 号
        if wife_in_group:
            chain.append(Comp.At(qq=wife_id))
        else:
            chain.append(Comp.Plain(f" {wife_id}"))
        
        chain.append(Comp.Plain("\n"))
        chain.append(Comp.Image.fromURL(avatar_url))
        
        return event.chain_result(chain)

    async def terminate(self):
        if self.scheduler.running:
            self.scheduler.shutdown()
        logger.info("今日老婆插件已安全卸载。")
