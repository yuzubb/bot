import discord
from discord import app_commands

# 許可するユーザーIDをここに直接入力してください
# 例: [1234567890, 9876543210]
ALLOWED_USER_IDS = []

def is_allowed():
    """権限チェック用デコレータ (ID直書き版)"""
    async def predicate(interaction: discord.Interaction) -> bool:
        # 1. Botのオーナーなら無条件で許可
        if await interaction.client.is_owner(interaction.user):
            return True
            
        # 2. サーバーのオーナーなら無条件で許可
        if interaction.guild and interaction.user.id == interaction.guild.owner_id:
            return True

        # 3. 指定したIDリストに含まれているかチェック
        if interaction.user.id in ALLOWED_USER_IDS:
            return True
            
        # 権限がない場合のメッセージを表示
        if not interaction.response.is_done():
            await interaction.response.send_message("🚫 権限がありません。管理者のみ実行可能です。", ephemeral=True)
        return False

    return app_commands.check(predicate)