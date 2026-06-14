import discord
from discord.ext import commands, tasks
from discord import app_commands
import json
import random
import time
import os

FILE = "giveaways.json"

def load_data():
    if not os.path.exists(FILE):
        return {}
    with open(FILE, "r", encoding="utf8") as f:
        return json.load(f)

def save_data(data):
    with open(FILE, "w", encoding="utf8") as f:
        json.dump(data, f, indent=4)

giveaways = load_data()

# -----------------------------
# 時間変換
# -----------------------------

def parse_time(time_str):

    time_str = time_str.lower()

    if time_str.endswith("s"):
        return int(time_str[:-1])

    if time_str.endswith("m"):
        return int(time_str[:-1]) * 60

    if time_str.endswith("h"):
        return int(time_str[:-1]) * 3600

    if time_str.endswith("d"):
        return int(time_str[:-1]) * 86400

    return int(time_str)


# -----------------------------
# Modal
# -----------------------------

class GiveawayModal(discord.ui.Modal, title="Giveaway設定"):

    prize = discord.ui.TextInput(label="景品")

    winners = discord.ui.TextInput(
        label="当選人数",
        default="1"
    )

    duration = discord.ui.TextInput(
        label="時間",
        placeholder="例: 10m / 2h / 1d / 30s"
    )

    async def on_submit(self, interaction: discord.Interaction):

        await interaction.response.defer(ephemeral=True)

        prize = self.prize.value
        winners = int(self.winners.value)
        duration = parse_time(self.duration.value)

        end = int(time.time()) + duration

        embed = discord.Embed(
            title="🎉 GIVEAWAY",
            description=f"🎁 景品: **{prize}**\n\n🎉で参加",
            color=discord.Color.orange()
        )

        embed.add_field(name="当選者数", value=f"{winners}人")
        embed.add_field(name="参加人数", value="0人")
        embed.add_field(name="終了", value=f"<t:{end}:R>")
        embed.set_footer(text=f"開催者: {interaction.user}")

        msg = await interaction.channel.send(embed=embed)

        await msg.add_reaction("🎉")

        giveaways[str(msg.id)] = {
            "channel": interaction.channel.id,
            "host": interaction.user.id,
            "prize": prize,
            "winners": winners,
            "end": end
        }

        save_data(giveaways)

        await interaction.followup.send("Giveaway開始", ephemeral=True)


# -----------------------------
# Cog
# -----------------------------

class Giveaway(commands.Cog):

    def __init__(self, bot):
        self.bot = bot
        self.check_giveaway.start()

    # 作成
    @app_commands.command(name="giveaway", description="Giveaway作成")
    async def giveaway(self, interaction: discord.Interaction):
        await interaction.response.send_modal(GiveawayModal())

    # 再抽選
    @app_commands.command(name="reroll", description="再抽選")
    async def reroll(self, interaction: discord.Interaction, message_id: str):

        if message_id not in giveaways:
            await interaction.response.send_message("存在しません", ephemeral=True)
            return

        data = giveaways[message_id]

        channel = self.bot.get_channel(data["channel"])
        message = await channel.fetch_message(int(message_id))

        reaction = message.reactions[0]
        users = [u async for u in reaction.users() if not u.bot]

        if not users:
            await interaction.response.send_message("参加者なし", ephemeral=True)
            return

        winners = random.sample(users, min(data["winners"], len(users)))

        text = " ".join([u.mention for u in winners])

        await interaction.response.send_message(f"🎉 再抽選当選者\n{text}")

    # 強制終了
    @app_commands.command(name="giveaway_end", description="Giveaway強制終了")
    async def giveaway_end(self, interaction: discord.Interaction, message_id: str):

        if message_id not in giveaways:
            await interaction.response.send_message("存在しません", ephemeral=True)
            return

        data = giveaways[message_id]

        channel = self.bot.get_channel(data["channel"])

        message = await channel.fetch_message(int(message_id))

        reaction = message.reactions[0]
        users = [u async for u in reaction.users() if not u.bot]

        if users:
            winners = random.sample(users, min(data["winners"], len(users)))
            winner_text = " ".join([u.mention for u in winners])
        else:
            winner_text = "なし"

        embed = discord.Embed(
            title="🎉 GIVEAWAY 強制終了",
            color=discord.Color.red()
        )

        embed.add_field(name="当選者", value=winner_text, inline=False)
        embed.add_field(name="景品", value=data["prize"], inline=False)

        await channel.send(embed=embed)

        del giveaways[message_id]
        save_data(giveaways)

        await interaction.response.send_message("終了しました", ephemeral=True)

    # -----------------------------
    # 自動終了
    # -----------------------------

    @tasks.loop(seconds=10)
    async def check_giveaway(self):

        now = int(time.time())
        data = load_data()

        for msg_id, g in list(data.items()):

            if now >= g["end"]:

                channel = self.bot.get_channel(g["channel"])

                try:
                    message = await channel.fetch_message(int(msg_id))
                except:
                    continue

                reaction = message.reactions[0]
                users = [u async for u in reaction.users() if not u.bot]

                if users:
                    winners = random.sample(users, min(g["winners"], len(users)))
                    winner_text = " ".join([u.mention for u in winners])
                else:
                    winner_text = "なし"

                embed = discord.Embed(
                    title="🎉 GIVEAWAY END",
                    color=discord.Color.dark_gray()
                )

                embed.add_field(
                    name="当選者発表",
                    value=f"{winner_text} が当選しました。",
                    inline=False
                )

                embed.add_field(
                    name="当選者数",
                    value=f"{g['winners']}人",
                    inline=False
                )

                embed.add_field(
                    name="開催者",
                    value=f"<@{g['host']}>",
                    inline=False
                )

                embed.add_field(
                    name="景品",
                    value=g["prize"],
                    inline=False
                )

                await channel.send(embed=embed)

                del data[msg_id]

        save_data(data)

    # -----------------------------
    # 参加人数更新
    # -----------------------------

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload):

        if str(payload.message_id) not in giveaways:
            return

        channel = self.bot.get_channel(payload.channel_id)
        message = await channel.fetch_message(payload.message_id)

        reaction = message.reactions[0]
        users = [u async for u in reaction.users() if not u.bot]

        embed = message.embeds[0]

        embed.set_field_at(
            1,
            name="参加人数",
            value=f"{len(users)}人"
        )

        await message.edit(embed=embed)


async def setup(bot):
    await bot.add_cog(Giveaway(bot))