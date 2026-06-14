import discord
from discord.ext import commands
from discord import app_commands
import json
import os
import io
from datetime import datetime

DATA_FILE = "ticket_panels.json"
COUNTER_FILE = "ticket_counter.json"


def load_json(file, default):
    if not os.path.exists(file):
        with open(file, "w", encoding="utf-8") as f:
            json.dump(default, f, indent=4)

    with open(file, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(file, data):
    with open(file, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4)


panels = load_json(DATA_FILE, {})
counter = load_json(COUNTER_FILE, {"count": 0})


# =========================
# チケット削除（永続対応）
# =========================
class CloseView(discord.ui.View):

    def __init__(self, log_channel_id=None, creator_id=None):
        super().__init__(timeout=None)
        self.log_channel_id = log_channel_id
        self.creator_id = creator_id

    @discord.ui.button(
        label="チケットを閉じる",
        style=discord.ButtonStyle.red,
        custom_id="ticket_close"
    )
    async def close(self, interaction: discord.Interaction, button: discord.ui.Button):

        channel = interaction.channel
        guild = interaction.guild

        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        logs = []
        async for msg in channel.history(limit=None, oldest_first=True):
            logs.append(f"<p><b>{msg.author}</b>: {msg.content}</p>")

        html = f"""
<html>
<body>
<h2>{channel.name}</h2>
{''.join(logs)}
</body>
</html>
"""

        file = discord.File(
            fp=io.BytesIO(html.encode("utf-8")),
            filename=f"{channel.name}.html"
        )

        log_ch = guild.get_channel(self.log_channel_id)

        creator = guild.get_member(self.creator_id)

        embed = discord.Embed(
            title="チケットログ",
            color=0x2b2d31
        )

        embed.add_field(
            name="作成者",
            value=creator.mention if creator else "不明",
            inline=True
        )

        embed.add_field(
            name="削除者",
            value=interaction.user.mention,
            inline=True
        )

        embed.add_field(
            name="削除日時",
            value=now,
            inline=False
        )

        embed.add_field(
            name="チャンネル名",
            value=channel.name,
            inline=False
        )

        if log_ch:
            await log_ch.send(embed=embed)
            await log_ch.send(file=file)

        await interaction.response.send_message("削除しました", ephemeral=True)
        await channel.delete()


# =========================
# チケット作成
# =========================
class TicketCreateView(discord.ui.View):

    def __init__(self, category_id, log_channel_id):
        super().__init__(timeout=None)
        self.category_id = category_id
        self.log_channel_id = log_channel_id

    @discord.ui.button(
        label="チケット作成",
        style=discord.ButtonStyle.green,
        custom_id="ticket_create"
    )
    async def create(self, interaction: discord.Interaction, button: discord.ui.Button):

        global counter

        if "count" not in counter:
            counter["count"] = 0

        counter["count"] += 1
        save_json(COUNTER_FILE, counter)

        num = str(counter["count"]).zfill(3)
        name = f"ticket-{num}"

        guild = interaction.guild
        category = guild.get_channel(self.category_id)

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            interaction.user: discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                attach_files=True,
                embed_links=False,
                add_reactions=False,
                read_message_history=True
            )
        }

        channel = await guild.create_text_channel(
            name=name,
            category=category,
            overwrites=overwrites
        )

        embed = discord.Embed(
            title="お問い合わせ",
            description="スタッフが参りますのでしばらくお待ちください。要件を記入してください。",
            color=0x2b2d31
        )

        embed.set_footer(text="Developer @0jpxz")

        await channel.send(
            interaction.user.mention,
            embed=embed,
            view=CloseView(self.log_channel_id, interaction.user.id)
        )

        await interaction.response.send_message(
            f"{channel.mention} を作成しました",
            ephemeral=True
        )


# =========================
# COG
# =========================
class Ticket(commands.Cog):

    def __init__(self, bot):
        self.bot = bot

        # 🔥 永続ボタン復元（重要）
        bot.add_view(CloseView())

        for panel in panels.values():
            bot.add_view(
                TicketCreateView(panel["category"], panel["log"])
            )

    @app_commands.command(
        name="ticket_panel",
        description="チケットパネル作成"
    )
    async def panel(
        self,
        interaction: discord.Interaction,
        title: str,
        description: str,
        panel_channel: discord.TextChannel,
        ticket_category: discord.CategoryChannel,
        log_channel: discord.TextChannel,
        image: discord.Attachment = None
    ):

        embed = discord.Embed(
            title=title,
            description=description,
            color=0x2b2d31
        )

        if image:
            embed.set_image(url=image.url)

        embed.set_footer(text="Developer @0jpxz")

        view = TicketCreateView(ticket_category.id, log_channel.id)

        msg = await panel_channel.send(embed=embed, view=view)

        panels[str(msg.id)] = {
            "category": ticket_category.id,
            "log": log_channel.id
        }

        save_json(DATA_FILE, panels)

        await interaction.response.send_message(
            "チケットパネル作成完了",
            ephemeral=True
        )


async def setup(bot):
    await bot.add_cog(Ticket(bot))