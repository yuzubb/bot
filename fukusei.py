import discord
from discord.ext import commands
from discord import app_commands

class CategorySelect(discord.ui.Select):
    def __init__(self, categories):

        options = []

        for c in categories:
            options.append(
                discord.SelectOption(
                    label=c.name,
                    value=str(c.id)
                )
            )

        super().__init__(
            placeholder="複製するカテゴリーを選択",
            min_values=1,
            max_values=1,
            options=options
        )

    async def callback(self, interaction: discord.Interaction):

        category_id = int(self.values[0])
        guild = interaction.guild

        category = guild.get_channel(category_id)

        if not category:
            await interaction.response.send_message("カテゴリーが見つかりません", ephemeral=True)
            return

        # カテゴリー作成
        new_category = await guild.create_category(
            name=f"{category.name}-copy",
            overwrites=category.overwrites
        )

        # チャンネル複製
        for channel in category.channels:

            if isinstance(channel, discord.TextChannel):
                await guild.create_text_channel(
                    name=channel.name,
                    category=new_category,
                    overwrites=channel.overwrites
                )

            elif isinstance(channel, discord.VoiceChannel):
                await guild.create_voice_channel(
                    name=channel.name,
                    category=new_category,
                    overwrites=channel.overwrites
                )

        await interaction.response.send_message(
            f"✅ `{category.name}` を複製しました",
            ephemeral=True
        )


class CategoryView(discord.ui.View):
    def __init__(self, categories):
        super().__init__(timeout=None)
        self.add_item(CategorySelect(categories))


class CategoryClone(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="category_clone", description="カテゴリー複製パネル")
    async def category_clone(self, interaction: discord.Interaction):

        categories = interaction.guild.categories

        if not categories:
            await interaction.response.send_message("カテゴリーがありません", ephemeral=True)
            return

        view = CategoryView(categories)

        await interaction.response.send_message(
            "複製するカテゴリーを選択してください",
            view=view
        )


async def setup(bot):
    await bot.add_cog(CategoryClone(bot))