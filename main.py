import random
import logging
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api import logger
import astrbot.api.message_components as Comp

@register("群老婆", "author", "两两配对的高性能今日老婆插件", "1.0.0", "repo url")
class MyPlugin(Star):
    def __init__(self, context: Context): 
        super().__init__(context)
        self.config = context.get_config()
        
        self.active_users = {} 
        self.daily_marriages = {} 

        self.scheduler = AsyncIOScheduler()
        reset_hour = self.config.get("reset_hour", 4)
        self.scheduler.add_job(self.reset_daily_data, 'cron', hour=reset_hour, minute=0)
        self.scheduler.start()
        
        logger.info(f"今日老婆插件已启动，每天 {reset_hour}:00 重置配对关系。")

    async def reset_daily_data(self):
        self.active_users.clear()
        self.daily_marriages.clear()
        logger.info("今日发言记录及婚姻关系已清空重置。")

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def handle_all_messages(self, event: AstrMessageEvent):
        if event.message_str.startswith("/"):
            return

        group_id = event.get_group_id()
        user_id = event.get_sender_id()

        if not group_id:
            return

        if group_id not in self.active_users:
            self.active_users[group_id] = set()

        if user_id not in self.active_users[group_id]:
            self.active_users[group_id].add(user_id)
            logger.debug(f"已记录群 {group_id} 的新发言人员: {user_id}")

    @filter.command("今日老婆")
    async def marry_me(self, event: AstrMessageEvent):
        group_id = event.get_group_id()
        user_id = event.get_sender_id()
        
        if group_id not in self.daily_marriages:
            self.daily_marriages[group_id] = {}

        married_dict = self.daily_marriages[group_id]
        
        if user_id in married_dict:
            wife_id = married_dict[user_id]
            yield self.build_marriage_result(event, user_id, wife_id, is_new=False)
            return

        active_members = self.active_users.get(group_id, set())
        married_people = set(married_dict.keys())
        candidates = [uid for uid in active_members if uid not in married_people and uid != user_id]

        if not candidates:
            yield event.plain_result("没有落单的群友了，大家都有归宿了呜呜呜...")
            return

        selected_wife = random.choice(candidates)
        married_dict[user_id] = selected_wife
        married_dict[selected_wife] = user_id

        yield self.build_marriage_result(event, user_id, selected_wife, is_new=True)

    def build_marriage_result(self, event, user_id, wife_id, is_new=True):
        avatar_url = f"https://q.qlogo.cn/headimg_dl?dst_uin={wife_id}&spec=640"
        
        msg_text = " ✨ 恭喜！你今天的命定老婆是：" if is_new else " ❤️ 别贪心，你今天的法定老婆依然是："
        
        chain = [
            Comp.At(qq=user_id),
            Comp.Plain(msg_text),
            Comp.At(qq=wife_id),
            Comp.Plain("\n"),
            Comp.Image.fromURL(avatar_url)
        ]
        return event.chain_result(chain)

    async def terminate(self):
        if self.scheduler.running:
            self.scheduler.shutdown()
        logger.info("今日老婆插件已安全卸载。")