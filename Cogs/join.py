import discord
from discord.ext import commands
from datetime import datetime


class JoinLog(commands.Cog):

    def __init__(self, bot):
        self.bot = bot
        self.log_channel_id = None

    @commands.command()
    @commands.has_permissions(administrator=True)
    async def set_join_log(self, ctx, channel: discord.TextChannel):

        self.log_channel_id = channel.id
        await ctx.send(f"入室ログチャンネルを設定しました: {channel.mention}")

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):

        if not self.log_channel_id:
            return

        channel = self.bot.get_channel(self.log_channel_id)
        if not channel:
            return

        guild = member.guild

        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        member_count = guild.member_count

        embed = discord.Embed(
            title="ようこそ M SHIPへ",
            color=0x2ecc71,
            timestamp=datetime.utcnow()
        )

        embed.add_field(
            name="ユーザー",
            value=member.mention,
            inline=True
        )

        embed.add_field(
            name="ID",
            value=str(member.id),
            inline=True
        )

        embed.add_field(
            name="アカウント作成日",
            value=member.created_at.strftime("%Y-%m-%d %H:%M"),
            inline=False
        )

        # 🔥ここが追加（下部情報）
        embed.add_field(
            name="現在のメンバー数",
            value=str(member_count),
            inline=True
        )

        embed.add_field(
            name="時刻",
            value=now,
            inline=True
        )

        embed.set_thumbnail(url=member.display_avatar.url)
        embed.set_footer(text="Join Log System")

        await channel.send(embed=embed)


async def setup(bot):
    await bot.add_cog(JoinLog(bot))