#discord client that forwards gateway events to the app
from __future__ import annotations


import discord


#discord client


class UncordClient(discord.Client):
    def __init__(self, app) -> None:
        super().__init__()
        self.app = app

    async def on_ready(self) -> None:
        await self.app.onDiscordReady()

    async def on_message(self, m: discord.Message) -> None:
        await self.app.onDiscordMessage(m)

    async def on_raw_message_edit(self, payload) -> None:
        self.app.onDiscordEdit(payload)

    async def on_raw_message_delete(self, payload) -> None:
        self.app.onDiscordDelete(payload)

    async def on_raw_reaction_add(self, payload) -> None:
        self.app.onReactionChange(payload.channel_id, payload.message_id)

    async def on_raw_reaction_remove(self, payload) -> None:
        self.app.onReactionChange(payload.channel_id, payload.message_id)

    async def on_raw_reaction_clear(self, payload) -> None:
        self.app.onReactionChange(payload.channel_id, payload.message_id)

    async def on_raw_reaction_clear_emoji(self, payload) -> None:
        self.app.onReactionChange(payload.channel_id, payload.message_id)

    async def on_raw_message_ack(self, payload) -> None:
        self.app.onDiscordAck(payload)

    async def on_typing(self, channel, user, when) -> None:
        self.app.onDiscordTyping(channel, user)

    async def on_presence_update(self, before, after) -> None:
        self.app.onDiscordPresence(after)

    async def on_relationship_update(self, before, after) -> None:
        self.app.onDiscordPresence(after.user)

    async def on_relationship_add(self, rel) -> None:
        self.app.onRelationship(rel, added=True)

    async def on_relationship_remove(self, rel) -> None:
        self.app.onRelationship(rel, added=False)

    async def on_voice_state_update(self, member, before, after) -> None:
        self.app.onDiscordVoiceState(member, before, after)

    async def on_call_create(self, call) -> None:
        self.app.onDiscordCall(call)

    async def on_call_update(self, before, after) -> None:
        self.app.onDiscordCall(after)

    async def on_call_delete(self, call) -> None:
        self.app.onDiscordCall(call, ended=True)

    async def on_private_channel_create(self, channel) -> None:
        self.app.rebuildDms()

    async def on_guild_join(self, guild) -> None:
        self.app.buildSidebar()

    async def on_guild_remove(self, guild) -> None:
        self.app.buildSidebar()
