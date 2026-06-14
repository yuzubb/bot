import discord
from discord.ext import commands
from discord import app_commands
import json
import os
from Kyasher import Kyash, KyashError, KyashLoginError

# セッション保存用ファイル
KYASH_SESSION_FILE = "kyash_data.json"

class Vending(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.login_attempts = {} # OTP入力待ちの一時データ

    def _load_sessions(self):
        if os.path.exists(KYASH_SESSION_FILE):
            with open(KYASH_SESSION_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        return {}

    def _save_session(self, user_id, data):
        sessions = self._load_sessions()
        sessions[str(user_id)] = data
        with open(KYASH_SESSION_FILE, "w", encoding="utf-8") as f:
            json.dump(sessions, f, indent=4)

    def get_kyash(self, user_id):
        """保存されたセッションからKyashインスタンスを復元"""
        sessions = self._load_sessions()
        data = sessions.get(str(user_id))
        if not data:
            return None
        return Kyash(
            email=data.get("email"),
            password=data.get("password"),
            client_uuid=data.get("client_uuid"),
            installation_uuid=data.get("installation_uuid"),
            access_token=data.get("access_token")
        )

    # --- 認証コマンド ---

    @app_commands.command(name="kyash_login", description="Kyashにログインを開始します")
    @app_commands.describe(email="Kyashのメールアドレス", password="Kyashのパスワード")
    async def kyash_login(self, interaction: discord.Interaction, email: str, password: str):
        await interaction.response.defer(ephemeral=True)
        try:
            kyash = Kyash(email=email, password=password)
            # インスタンスを保持してOTP検証へ繋ぐ
            self.login_attempts[interaction.user.id] = {
                "instance": kyash, "email": email, "password": password
            }
            await interaction.followup.send("📲 SMSに届いた6桁のコードを `/kyash_verify` で入力してください。")
        except Exception as e:
            await interaction.followup.send(f"❌ エラーが発生しました: {e}")

    @app_commands.command(name="kyash_verify", description="OTPを入力して認証を完了します")
    @app_commands.describe(otp="SMSで届いた6桁の数字")
    async def kyash_verify(self, interaction: discord.Interaction, otp: str):
        await interaction.response.defer(ephemeral=True)
        attempt = self.login_attempts.get(interaction.user.id)
        
        if not attempt:
            return await interaction.followup.send("⚠️ 先に `/kyash_login` を実行してください。")

        try:
            kyash = attempt["instance"]
            kyash.login(otp)
            
            # セッション情報を永続化（次回からOTP不要）
            self._save_session(interaction.user.id, {
                "email": attempt["email"],
                "password": attempt["password"],
                "client_uuid": kyash.client_uuid,
                "installation_uuid": kyash.installation_uuid,
                "access_token": kyash.access_token
            })
            
            del self.login_attempts[interaction.user.id]
            await interaction.followup.send("✅ ログイン成功！セッションが保存されました。")
        except Exception as e:
            await interaction.followup.send(f"❌ 認証失敗: {e}")


async def setup(bot: commands.Bot):
    await bot.add_cog(Vending(bot))