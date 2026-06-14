import discord
from discord import app_commands
from discord.ext import commands
import json
import os

DATA_FILE = "review_panels.json"


def load_panels():
    if not os.path.exists(DATA_FILE):
        return []

    with open(DATA_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_panels(data):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4)


# ==============================
# 実績入力モーダル
# ==============================
class ReviewModal(discord.ui.Modal):

    def __init__(self, target_channel: discord.TextChannel):

        super().__init__(title="実績記入")

        self.target_channel = target_channel

        self.item_name = discord.ui.TextInput(
            label="購入商品",
            required=True
        )

        self.item_count = discord.ui.TextInput(
            label="購入数",
            required=True
        )

        self.review_text = discord.ui.TextInput(
            label="感想",
            style=discord.TextStyle.paragraph,
            required=True,
            max_length=300
        )

        self.rating = discord.ui.TextInput(
            label="評価 (1~5)",
            min_length=1,
            max_length=1,
            required=True
        )

        self.add_item(self.item_name)
        self.add_item(self.item_count)
        self.add_item(self.review_text)
        self.add_item(self.rating)

    async def on_submit(self, interaction: discord.Interaction):

        raw_rating = self.rating.value

        if not raw_rating.isdigit() or not (1 <= int(raw_rating) <= 5):
            return await interaction.response.send_message(
                "評価は1〜5で入力してください",
                ephemeral=True
            )

        stars = "⭐" * int(raw_rating)

        embed = discord.Embed(
            title="実績",
            color=0x2b2d31
        )

        embed.set_author(
            name=f"実績記入ユーザー | {interaction.user.display_name}",
            icon_url=interaction.user.display_avatar.url
        )

        embed.add_field(
            name="購入商品",
            value=f"```\n{self.item_name.value}\n```",
            inline=False
        )

        embed.add_field(
            name="購入数",
            value=f"```\n{self.item_count.value}個\n```",
            inline=False
        )

        embed.add_field(
            name="評価",
            value=f"```\n{stars} ({raw_rating})\n```",
            inline=False
        )

        embed.add_field(
            name="感想",
            value=f"```\n{self.review_text.value}\n```",
            inline=False
        )

        embed.set_footer(text="Developer by @m_shoppp")

        await self.target_channel.send(embed=embed)

        await interaction.response.send_message(
            "✅ 実績を投稿しました",
            ephemeral=True
        )


# ==============================
# ボタン
# ==============================
class ReviewPanelView(discord.ui.View):

    def __init__(self, channel_id: int):

        super().__init__(timeout=None)

        self.channel_id = channel_id

    @discord.ui.button(
        label="実績記入",
        style=discord.ButtonStyle.success,
        custom_id="persistent_review_button"
    )
    async def start_review(self, interaction: discord.Interaction, button: discord.ui.Button):

        channel = interaction.guild.get_channel(self.channel_id)

        if channel is None:
            return await interaction.response.send_message(
                "送信チャンネルが見つかりません",
                ephemeral=True
            )

        await interaction.response.send_modal(
            ReviewModal(channel)
        )


# ==============================
# Cog
# ==============================
class ReviewCog(commands.Cog):

    def __init__(self, bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_ready(self):

        panels = load_panels()

        for panel in panels:

            self.bot.add_view(
                ReviewPanelView(panel["channel_id"])
            )

        print(f"Review panels restored: {len(panels)}")

    @app_commands.command(
        name="review_panel",
        description="実績記入パネルを設置"
    )
    @app_commands.describe(
        channel="実績送信チャンネル"
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def review_panel(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel
    ):

        embed = discord.Embed(
            title="実績記入パネル",
            description="以下のボタンから実績を記入してください。",
            color=0x3498db
        )

        embed.set_footer(text="Developer by @0jpxz")

        view = ReviewPanelView(channel.id)

        message = await interaction.channel.send(
            embed=embed,
            view=view
        )

        panels = load_panels()

        panels.append({
            "guild_id": interaction.guild.id,
            "channel_id": channel.id,
            "message_id": message.id
        })

        save_panels(panels)

        await interaction.response.send_message(
            "✅ パネルを設置しました",
            ephemeral=True
        )


async def setup(bot):
    await bot.add_cog(ReviewCog(bot))