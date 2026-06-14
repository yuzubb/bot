import discord
from discord import app_commands
from discord.ext import commands

class ModerationCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        # Bot導入ボタンのリンク先URLを設定してください
        self.invite_url = "https://discord.gg/daUDCrPeNK"

    @app_commands.command(name="nuke", description="チャンネルのメッセージログをすべて削除して再作成します")
    @app_commands.checks.has_permissions(manage_channels=True)
    async def nuke(self, interaction: discord.Interaction):
        # 1. 元のチャンネル情報を取得
        channel = interaction.channel
        channel_position = channel.position

        # 2. 新しいチャンネルを同じ設定で作成
        new_channel = await channel.clone(reason=f"Nukeコマンド実行者: {interaction.user}")
        await new_channel.edit(position=channel_position)

        # 3. 古いチャンネルを削除
        await channel.delete(reason="Nukeコマンドによる一括削除")

        # --- 4. 画像のデザインを再現したEmbedの作成 ---
        embed = discord.Embed(
            title="Nuke",
            description=f"{interaction.user.mention} がチャンネルのメッセージログを全て削除しました",
            color=0x2ecc71 # 画像に基づいた緑色
        )
        embed.set_footer(text="Created by @m_shoppp")

        # 5. 「Bot導入」ボタンの作成
        view = discord.ui.View()
        view.add_item(discord.ui.Button(
            label="Botサポート鯖", 
            style=discord.ButtonStyle.link, 
            url=self.invite_url
        ))

        # 新しいチャンネルにメッセージを送信
        await new_channel.send(embed=embed, view=view)

async def setup(bot):
    await bot.add_cog(ModerationCog(bot))