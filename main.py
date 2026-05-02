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


@register("群老婆", "xiaolongxia521", "支持分群固定配对，可设置当老婆不在群时的提示文本，凌晨4点重置，支持强娶、离婚功能，允许拥有多个群老婆。新增活跃天数配置，优化永恒老婆功能。", "3.0.0", "https://github.com/xiaolongxia521/astrbot_plugin_today_wife")
class MyPlugin(Star):
    def __init__(self, context: Context, config: Optional[Dict] = None): 
        super().__init__(context)
        
        self.plugin_config = config or {}
        
        if not self.plugin_config:
            self.plugin_config = self._load_config_from_file()
        
        self.active_users: Dict[str, Dict[str, Set[str]]] = defaultdict(lambda: defaultdict(set))
        self.daily_marriages: Dict[str, Dict[str, List[str]]] = defaultdict(dict)
        self.fixed_pairings: Dict[str, Dict[str, Dict]] = {}
        self.locks = defaultdict(asyncio.Lock)
        
        self._load_fixed_pairings()
        
        reset_hour = self.plugin_config.get("reset_hour", 4)
        self.scheduler = AsyncIOScheduler()
        self.scheduler.add_job(self.reset_daily_data, 'cron', hour=reset_hour, minute=0)
        self.scheduler.start()
        
        fixed_count = sum(len(p) for p in self.fixed_pairings.values())
        logger.info(f"今日老婆插件已启动，每天 {reset_hour}:00 重置数据。固定配对数: {fixed_count}")

    def _load_config_from_file(self) -> Dict:
        known_path = "/AstrBot/data/config/astrbot_plugin_today_wife_config.json"
        
        if os.path.exists(known_path):
            try:
                with open(known_path, 'r', encoding='utf-8') as f:
                    config = json.load(f)
                    logger.info(f"从文件 {known_path} 读取配置成功")
                    return config
            except Exception as e:
                logger.error(f"读取配置文件失败: {e}")
                return self._get_default_config()
        
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
        return self._get_default_config()

    def _get_default_config(self):
        return {
            "reset_hour": 4,
            "active_days": 1,
            "fixed_pairings": "{}",
            "enable_random_pairing": True,
            "not_in_group_text": "你绑定的老婆今天不在这个群里哦~",
            "max_random_count": 5,
            "max_marry_count": 3,
            "max_divorce_count": 2
        }

    def _load_fixed_pairings(self):
        """从配置文件加载固定配对（支持 text 类型的 JSON 字符串）"""
        fixed_config = self.plugin_config.get("fixed_pairings", "{}").strip()
        
        logger.info(f"处理 fixed_pairings: {repr(fixed_config)}")
        
        if not fixed_config:
            logger.info("未配置固定配对，将使用随机配对模式")
            return
        
        try:
            if isinstance(fixed_config, str):
                parsed_config = json.loads(fixed_config)
            else:
                parsed_config = fixed_config
                
            if isinstance(parsed_config, dict):
                for group_id, user_wife_map in parsed_config.items():
                    if not isinstance(user_wife_map, dict):
                        continue
                    
                    if group_id not in self.fixed_pairings:
                        self.fixed_pairings[group_id] = {}
                    
                    for user_qq, wife_qq in user_wife_map.items():
                        self.fixed_pairings[group_id][user_qq] = {
                            "wife": wife_qq,
                            "text": None
                        }
                        logger.info(f"加载固定配对: 群{group_id} - {user_qq} <-> {wife_qq}")
            
            total = sum(len(p) for p in self.fixed_pairings.values())
            logger.info(f"成功加载 {len(self.fixed_pairings)} 个群的 {total} 对固定配对")
            
        except Exception as e:
            logger.error(f"解析固定配对配置失败: {e}")
            logger.warning("将使用随机配对模式")

    def get_fixed_pairing(self, group_id: str, user_id: str) -> Optional[Dict]:
        """获取用户的固定配对"""
        if group_id in self.fixed_pairings:
            return self.fixed_pairings[group_id].get(user_id)
        return None

    async def reset_daily_data(self):
        """每日重置"""
        import datetime
        today_str = datetime.datetime.now().strftime("%Y-%m-%d")
        
        for group_id in list(self.active_users.keys()):
            days_to_keep = self.plugin_config.get("active_days", 1)
            current_day = datetime.datetime.now()
            kept_days = []
            
            for day_str in list(self.active_users[group_id].keys()):
                day = datetime.datetime.strptime(day_str, "%Y-%m-%d")
                days_diff = (current_day - day).days
                if days_diff < days_to_keep:
                    kept_days.append(day_str)
                else:
                    del self.active_users[group_id][day_str]
        
        for group_id in list(self.daily_marriages.keys()):
            self.daily_marriages[group_id].clear()
            
            if group_id in self.fixed_pairings:
                for user_id, info in self.fixed_pairings[group_id].items():
                    wife_id = info["wife"]
                    self.daily_marriages[group_id][user_id] = [wife_id]
                    if wife_id not in self.daily_marriages[group_id]:
                        self.daily_marriages[group_id][wife_id] = []
                    if user_id not in self.daily_marriages[group_id][wife_id]:
                        self.daily_marriages[group_id][wife_id].append(user_id)
        
        logger.info("每日发言记录已重置，固定配对已保留。")

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def handle_all_messages(self, event: AstrMessageEvent):
        """记录发言用户"""
        if not event.message_str or event.message_str.startswith("/"):
            return

        import datetime
        group_id = event.get_group_id()
        user_id = event.get_sender_id()
        today_str = datetime.datetime.now().strftime("%Y-%m-%d")
        
        if not group_id: 
            return

        if self.get_fixed_pairing(group_id, user_id):
            return

        async with self.locks[group_id]:
            self.active_users[group_id][today_str].add(user_id)

    async def _get_group_members(self, event: AstrMessageEvent, group_id: str) -> Set[str]:
        """获取群成员列表"""
        try:
            group = await event.get_group(group_id)
            if group and group.members:
                return set(str(m.user_id) for m in group.members)
            return set()
        except Exception as e:
            logger.warning(f"获取群成员失败: {e}")
            return set()

    async def _get_group_member_nickname(self, event: AstrMessageEvent, user_qq: str) -> str:
        """根据QQ号获取群昵称"""
        try:
            group = await event.get_group(event.get_group_id())
            if group and group.members:
                for member in group.members:
                    if str(member.user_id) == user_qq:
                        return member.nickname or user_qq
            return user_qq
        except Exception as e:
            logger.warning(f"获取用户昵称失败: {e}")
            return user_qq

    async def _parse_target_qq(self, event: AstrMessageEvent) -> Optional[str]:
        """解析目标QQ号"""
        # 1. 尝试从事件参数中解析
        message_str = event.message_str.strip()
        if message_str:
            # 检查是否是QQ号
            if message_str.isdigit() and len(message_str) >= 5 and len(message_str) <= 12:
                return message_str
                
            # 检查是否包含QQ号
            import re
            qq_match = re.search(r'(\d{5,12})', message_str)
            if qq_match:
                return qq_match.group(1)
        
        # 2. 尝试从消息链中解析@成员
        if hasattr(event, 'message_obj') and hasattr(event.message_obj, 'chain'):
            for component in event.message_obj.chain:
                if hasattr(component, 'qq'):
                    return str(component.qq)
                elif hasattr(component, 'data') and 'qq' in component.data:
                    return str(component.data['qq'])
        
        return None

    async def _get_active_candidates(self, group_id: str, user_id: str) -> List[str]:
        """获取活跃用户候选人"""
        import datetime
        
        candidates = set()
        days_to_keep = self.plugin_config.get("active_days", 1)
        current_day = datetime.datetime.now()
        
        for day_str in list(self.active_users[group_id].keys()):
            day = datetime.datetime.strptime(day_str, "%Y-%m-%d")
            days_diff = (current_day - day).days
            if days_diff < days_to_keep:
                candidates.update(self.active_users[group_id][day_str])
        
        # 移除已经是老婆的用户，避免重复
        if group_id in self.daily_marriages and user_id in self.daily_marriages[group_id]:
            existing_wives = self.daily_marriages[group_id][user_id]
            candidates = [uid for uid in candidates if uid != user_id and uid not in existing_wives]
        
        return candidates

    @filter.command("今日老婆")
    async def show_wives(self, event: AstrMessageEvent):
        """显示所有老婆关系"""
        group_id = event.get_group_id()
        user_id = event.get_sender_id()
        
        if not group_id:
            yield event.plain_result("此功能仅限群聊使用。")
            return

        async with self.locks[group_id]:
            if group_id not in self.daily_marriages or user_id not in self.daily_marriages[group_id]:
                yield event.plain_result("你还没有群老婆哦~")
                return
            
            yield await self.build_marriage_result(event, user_id, self.daily_marriages[group_id][user_id])

    @filter.command("随机老婆")
    async def marry_me(self, event: AstrMessageEvent):
        """随机配对功能：优先使用固定配对，否则使用随机配对"""
        group_id = event.get_group_id()
        user_id = event.get_sender_id()
        
        if not group_id:
            yield event.plain_result("此功能仅限群聊使用。")
            return

        async with self.locks[group_id]:
            if group_id not in self.daily_marriages:
                self.daily_marriages[group_id] = {}
            
            if user_id not in self.daily_marriages[group_id]:
                self.daily_marriages[group_id][user_id] = []
            
            fixed_info = self.get_fixed_pairing(group_id, user_id)
            if fixed_info:
                fixed_wife = fixed_info["wife"]
                not_in_group_text = fixed_info.get("text")
                
                group_members = await self._get_group_members(event, group_id)
                wife_in_group = fixed_wife in group_members if group_members else False
                
                if not wife_in_group:
                    if not_in_group_text:
                        yield event.plain_result(not_in_group_text)
                    else:
                        default_text = self.plugin_config.get("not_in_group_text", "你绑定的老婆今天不在这个群里哦~")
                        yield event.plain_result(default_text)
                    return
                
                if fixed_wife not in self.daily_marriages[group_id][user_id]:
                    self.daily_marriages[group_id][user_id].append(fixed_wife)
                
                if fixed_wife not in self.daily_marriages[group_id]:
                    self.daily_marriages[group_id][fixed_wife] = []
                if user_id not in self.daily_marriages[group_id][fixed_wife]:
                    self.daily_marriages[group_id][fixed_wife].append(user_id)
                
                yield await self.build_random_result(event, user_id, self.daily_marriages[group_id][user_id])
                return
            
            enable_random = self.plugin_config.get("enable_random_pairing", True)
            if not enable_random:
                yield event.plain_result("随机配对功能已关闭，且你未在固定配对列表中。")
                return
            
            active_members = await self._get_active_candidates(group_id, user_id)
            
            if not active_members:
                yield event.plain_result("没有活跃的群友可以配对...")
                return

            selected_wife = random.choice(active_members)
            self.daily_marriages[group_id][user_id].append(selected_wife)
            
            if selected_wife not in self.daily_marriages[group_id]:
                self.daily_marriages[group_id][selected_wife] = []
            if user_id not in self.daily_marriages[group_id][selected_wife]:
                self.daily_marriages[group_id][selected_wife].append(user_id)
            
            logger.info(f"群 {group_id} 随机配对成功: {user_id} & {selected_wife}")
            yield await self.build_random_result(event, user_id, self.daily_marriages[group_id][user_id])

    @filter.command("强娶")
    async def force_marry(self, event: AstrMessageEvent):
        """强娶功能：强制绑定指定用户"""
        group_id = event.get_group_id()
        user_id = event.get_sender_id()
        
        if not group_id:
            yield event.plain_result("此功能仅限群聊使用。")
            return
        
        target_qq = await self._parse_target_qq(event)
        
        if not target_qq:
            yield event.plain_result("未能识别到有效的QQ号。请使用 @成员 或输入 QQ号。")
            return
        
        if target_qq == user_id:
            yield event.plain_result("你不能强娶自己哦~")
            return
        
        async with self.locks[group_id]:
            if group_id not in self.daily_marriages:
                self.daily_marriages[group_id] = {}
            
            if user_id not in self.daily_marriages[group_id]:
                self.daily_marriages[group_id][user_id] = []
            
            if target_qq not in self.daily_marriages[group_id]:
                self.daily_marriages[group_id][target_qq] = []
            
            if target_qq not in self.daily_marriages[group_id][user_id]:
                self.daily_marriages[group_id][user_id].append(target_qq)
            
            if user_id not in self.daily_marriages[group_id][target_qq]:
                self.daily_marriages[group_id][target_qq].append(user_id)
            
            logger.info(f"群 {group_id} 强娶成功: {user_id} & {target_qq}")
            yield await self.build_marry_result(event, user_id, self.daily_marriages[group_id][user_id])

    @filter.command("离婚")
    async def divorce(self, event: AstrMessageEvent):
        """离婚功能：解除与指定老婆的关系"""
        group_id = event.get_group_id()
        user_id = event.get_sender_id()
        
        if not group_id:
            yield event.plain_result("此功能仅限群聊使用。")
            return
        
        target_qq = await self._parse_target_qq(event)
        
        if not target_qq:
            yield event.plain_result("未能识别到有效的QQ号。请使用 @成员 或输入 QQ号。")
            return
        
        async with self.locks[group_id]:
            if group_id not in self.daily_marriages or user_id not in self.daily_marriages[group_id]:
                yield event.plain_result("你还没有群老婆，无需离婚。")
                return
            
            if target_qq not in self.daily_marriages[group_id][user_id]:
                yield event.plain_result("你和这个人没有婚姻关系。")
                return
            
            self.daily_marriages[group_id][user_id].remove(target_qq)
            if not self.daily_marriages[group_id][user_id]:
                del self.daily_marriages[group_id][user_id]
            
            if target_qq in self.daily_marriages[group_id]:
                if user_id in self.daily_marriages[group_id][target_qq]:
                    self.daily_marriages[group_id][target_qq].remove(user_id)
                    if not self.daily_marriages[group_id][target_qq]:
                        del self.daily_marriages[group_id][target_qq]
            
            logger.info(f"群 {group_id} 离婚成功: {user_id} & {target_qq}")
            yield await self.build_divorce_result(event, user_id, self.daily_marriages.get(group_id, {}).get(user_id, []))

    async def build_marriage_result(self, event, user_id, wife_list):
        """构建结果消息链"""
        chain = [Comp.At(qq=user_id), Comp.Plain(" 你的群老婆有：")]
        
        for index, wife_id in enumerate(wife_list):
            if index > 0:
                chain.append(Comp.Plain("、"))
            
            # 获取群昵称
            nickname = await self._get_group_member_nickname(event, wife_id)
            chain.append(Comp.Plain(nickname))
        
        if len(wife_list) <= 2:
            for wife_id in wife_list:
                avatar_url = f"https://q.qlogo.cn/headimg_dl?dst_uin={wife_id}&spec=640"
                chain.append(Comp.Plain("\n"))
                chain.append(Comp.Image.fromURL(avatar_url))
        
        return event.chain_result(chain)

    async def build_random_result(self, event, user_id, wife_list):
        """构建随机老婆结果消息链"""
        chain = [Comp.At(qq=user_id), Comp.Plain(" 今天的随机老婆是：")]
        
        for index, wife_id in enumerate(wife_list):
            if index > 0:
                chain.append(Comp.Plain("、"))
            
            # 获取群昵称
            nickname = await self._get_group_member_nickname(event, wife_id)
            chain.append(Comp.Plain(nickname))
        
        if len(wife_list) <= 2:
            for wife_id in wife_list:
                avatar_url = f"https://q.qlogo.cn/headimg_dl?dst_uin={wife_id}&spec=640"
                chain.append(Comp.Plain("\n"))
                chain.append(Comp.Image.fromURL(avatar_url))
        
        chain.append(Comp.Plain(" 喵~"))
        return event.chain_result(chain)

    async def build_marry_result(self, event, user_id, wife_list):
        """构建强娶结果消息链"""
        chain = [Comp.At(qq=user_id), Comp.Plain(" 强娶成功！现在你的群老婆有：")]
        
        for index, wife_id in enumerate(wife_list):
            if index > 0:
                chain.append(Comp.Plain("、"))
            
            # 获取群昵称
            nickname = await self._get_group_member_nickname(event, wife_id)
            chain.append(Comp.Plain(nickname))
        
        if len(wife_list) <= 2:
            for wife_id in wife_list:
                avatar_url = f"https://q.qlogo.cn/headimg_dl?dst_uin={wife_id}&spec=640"
                chain.append(Comp.Plain("\n"))
                chain.append(Comp.Image.fromURL(avatar_url))
        
        chain.append(Comp.Plain(" 喵~"))
        return event.chain_result(chain)

    async def build_divorce_result(self, event, user_id, wife_list):
        """构建离婚结果消息链"""
        if not wife_list:
            return event.plain_result(f"{Comp.At(qq=user_id)} 离婚成功！你现在没有群老婆了~ 喵~")
        
        chain = [Comp.At(qq=user_id), Comp.Plain(" 离婚成功！现在你的群老婆有：")]
        
        for index, wife_id in enumerate(wife_list):
            if index > 0:
                chain.append(Comp.Plain("、"))
            
            # 获取群昵称
            nickname = await self._get_group_member_nickname(event, wife_id)
            chain.append(Comp.Plain(nickname))
        
        if len(wife_list) <= 2:
            for wife_id in wife_list:
                avatar_url = f"https://q.qlogo.cn/headimg_dl?dst_uin={wife_id}&spec=640"
                chain.append(Comp.Plain("\n"))
                chain.append(Comp.Image.fromURL(avatar_url))
        
        chain.append(Comp.Plain(" 喵~"))
        return event.chain_result(chain)

    async def terminate(self):
        if self.scheduler.running:
            self.scheduler.shutdown()
        logger.info("今日老婆插件已安全卸载。")
