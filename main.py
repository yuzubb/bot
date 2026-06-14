import discord
from discord.ext import commands
from discord import app_commands
import os
import traceback

# ===============================
# BOTトークン
# ===============================
TOKEN = ""


intents = discord.Intents.all()


# ===============================
# BOTクラス（安定版）
# ===============================
class MyBot(commands.Bot):

    async def setup_hook(self):

        base_dir = os.path.dirname(os.path.abspath(__file__))
        cogs_dir = os.path.join(base_dir, "Cogs")

        print(f"[INFO] Cogs dir: {cogs_dir}")

        if not os.path.exists(cogs_dir):
            print("[ERROR] Cogsフォルダが存在しません")
            return

        # ===========================
        # Cogロード
        # ===========================
        for file in os.listdir(cogs_dir):

            if file.endswith(".py") and not file.startswith("_"):

                ext = f"Cogs.{file[:-3]}"

                try:
                    await self.load_extension(ext)
                    print(f"[OK] Loaded Cog: {ext}")

                except Exception:
                    print(f"[NG] Failed Cog: {ext}")
                    traceback.print_exc()

        # ===========================
        # Slash同期（安定）
        # ===========================
        try:
            synced = await self.tree.sync()
            print(f"[INFO] Slash synced: {len(synced)}")

        except Exception:
            print("[ERROR] Slash sync failed")
            traceback.print_exc()


# ===============================
# BOT起動
# ===============================
bot = MyBot(
    command_prefix="$",
    intents=intents,
    help_command=None
)


# ===============================
# 起動ログ
# ===============================
@bot.event
async def on_ready():

    print("===================================")
    print(f"Bot logged in as {bot.user}")
    print(f"Bot ID: {bot.user.id}")
    print("===================================")


# ===============================
# Slashエラー処理
# ===============================
@bot.tree.error
async def on_app_command_error(interaction, error):

    print("[Slash Error]")
    traceback.print_exc()

    try:
        if interaction.response.is_done():
            await interaction.followup.send(
                "エラーが発生しました",
                ephemeral=True
            )
        else:
            await interaction.response.send_message(
                "エラーが発生しました",
                ephemeral=True
            )
    except:
        pass


# ===============================
# 起動
# ===============================
bot.run(TOKEN)