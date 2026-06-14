import discord
from discord.ext import commands
from discord import app_commands
import json
import os

DATA_FILE = "auth_panels.json"


def load_data():
    if not os.path.exists(DATA_FILE):
        return {}

    with open(DATA_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_data(data):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4)


# 永続ボタン
class AuthView(discord.ui.View):

    def __init__(self, role_id):
        super().__init__(timeout=None)
        self.role_id = role_id

    @discord.ui.button(
        label="認証する",
        style=discord.ButtonStyle.success,
        custom_id="auth_verify_button"
    )
    async def verify(self, interaction: discord.Interaction, button: discord.ui.Button):

        role = interaction.guild.get_role(self.role_id)

        if role in interaction.user.roles:
            await interaction.response.send_message(
                "既に認証済みです。",
                ephemeral=True
            )
            return

        await interaction.user.add_roles(role)

        await interaction.response.send_message(
            "認証完了しました。",
            ephemeral=True
        )


class AuthPanel(commands.Cog):

    def __init__(self, bot):
        self.bot = bot

        # 起動時にview登録
        data = load_data()

        for panel in data.values():
            bot.add_view(AuthView(panel["role_id"]))


    @app_commands.command(
        name="認証パネル作成",
        description="認証パネルを作成します"
    )
    @app_commands.describe(
        role="付与するロール",
        title="タイトル（任意）",
        description="説明（任意）",
        image="画像(PNG)"
    )
    async def create_panel(
        self,
        interaction: discord.Interaction,
        role: discord.Role,
        title: str = None,
        description: str = None,
        image: discord.Attachment = None
    ):

        embed = discord.Embed(color=0x2b2d31)

        if title:
            embed.title = title

        if description:
            embed.description = description

        embed.set_footer(text="Developer by @0jpxz")

        if image:
            embed.set_image(url=image.url)

        view = AuthView(role.id)

        msg = await interaction.channel.send(embed=embed, view=view)

        data = load_data()

        data[str(msg.id)] = {
            "role_id": role.id
        }

        save_data(data)

        await interaction.response.send_message(
            "認証パネルを作成しました。",
            ephemeral=True
        )


async def setup(bot):
    await bot.add_cog(AuthPanel(bot))