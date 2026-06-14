"""
antiraid.py - 超高性能荒らし対策 & 自動復旧システム

機能:
  - サーバー構造（カテゴリ/チャンネル/ロール/権限）を定期バックアップ
  - 短時間に大量削除を検知 → 実行者をBANまたはKICK
  - 削除されたカテゴリ/チャンネル/ロールを自動復旧
  - Webhookも復旧対象
  - 管理コマンド群
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from collections import defaultdict
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands, tasks

# ==============================
# 定数
# ==============================

DATA_FILE = "antiraid_data.json"
BACKUP_FILE = "server_backup.json"

# 検知閾値（秒あたりの削除数）
DEFAULT_THRESHOLD = 3          # この件数以上の削除が
DEFAULT_WINDOW    = 10         # この秒数以内に起きたらアウト

# ==============================
# ユーティリティ
# ==============================

def load_json(path: str, default=None):
    if default is None:
        default = {}
    if not os.path.exists(path):
        return default
    with open(path, "r", encoding="utf-8") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            return default

def save_json(path: str, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

def overwrites_to_dict(overwrites: dict) -> list:
    """PermissionOverwritesをJSONシリアライズ可能な形式に変換"""
    result = []
    for target, perm in overwrites.items():
        allow, deny = perm.pair()
        entry = {
            "allow": allow.value,
            "deny": deny.value,
        }
        if isinstance(target, discord.Role):
            entry["type"] = "role"
            entry["id"] = target.id
            entry["name"] = target.name
        elif isinstance(target, discord.Member):
            entry["type"] = "member"
            entry["id"] = target.id
            entry["name"] = str(target)
        result.append(entry)
    return result

def dict_to_overwrites(guild: discord.Guild, data: list) -> dict:
    """JSONからPermissionOverwritesを復元"""
    result = {}
    for entry in data:
        allow = discord.Permissions(entry["allow"])
        deny  = discord.Permissions(entry["deny"])
        perm  = discord.PermissionOverwrite.from_pair(allow, deny)
        if entry["type"] == "role":
            target = guild.get_role(entry["id"])
            # IDで見つからなければ名前で検索
            if target is None:
                target = discord.utils.get(guild.roles, name=entry["name"])
        else:
            target = guild.get_member(entry["id"])
        if target:
            result[target] = perm
    return result

# ==============================
# バックアップ取得
# ==============================

def snapshot_guild(guild: discord.Guild) -> dict:
    """サーバー全体のスナップショットを取る"""
    snap = {
        "guild_id": guild.id,
        "timestamp": time.time(),
        "roles": [],
        "categories": [],
        "channels": [],
    }

    # --- ロール ---
    for role in guild.roles:
        if role.is_default():
            continue
        snap["roles"].append({
            "id": role.id,
            "name": role.name,
            "color": role.color.value,
            "hoist": role.hoist,
            "mentionable": role.mentionable,
            "permissions": role.permissions.value,
            "position": role.position,
        })

    # --- カテゴリ ---
    for cat in guild.categories:
        snap["categories"].append({
            "id": cat.id,
            "name": cat.name,
            "position": cat.position,
            "overwrites": overwrites_to_dict(cat.overwrites),
        })

    # --- チャンネル（テキスト/ボイス/フォーラム等） ---
    for ch in guild.channels:
        if isinstance(ch, discord.CategoryChannel):
            continue
        entry = {
            "id": ch.id,
            "name": ch.name,
            "type": str(ch.type),
            "position": ch.position,
            "category_id": ch.category_id,
            "overwrites": overwrites_to_dict(ch.overwrites),
            "topic": None,
            "nsfw": False,
            "slowmode_delay": 0,
            "bitrate": None,
            "user_limit": None,
        }
        if isinstance(ch, discord.TextChannel):
            entry["topic"]         = ch.topic
            entry["nsfw"]          = ch.nsfw
            entry["slowmode_delay"] = ch.slowmode_delay
        elif isinstance(ch, discord.VoiceChannel):
            entry["bitrate"]    = ch.bitrate
            entry["user_limit"] = ch.user_limit
        snap["channels"].append(entry)

    return snap

# ==============================
# Cog本体
# ==============================

class AntiRaid(commands.Cog):

    def __init__(self, bot: commands.Bot):
        self.bot = bot

        # 設定読み込み
        self.config: dict = load_json(DATA_FILE, {})

        # 削除イベントの時刻キュー  guild_id -> {"channel": [...], "category": [...], "role": [...]}
        self._delete_times: dict[int, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))

        # 復旧中フラグ（二重復旧防止）
        self._recovering: set[int] = set()

        # 定期バックアップ起動
        self.auto_backup.start()

    def cog_unload(self):
        self.auto_backup.cancel()

    # ==============================
    # 設定ヘルパー
    # ==============================

    def _guild_cfg(self, guild_id: int) -> dict:
        """ギルド設定を返す（なければデフォルト）"""
        return self.config.setdefault(str(guild_id), {
            "enabled": False,
            "action": "ban",            # ban / kick
            "threshold": DEFAULT_THRESHOLD,
            "window": DEFAULT_WINDOW,
            "log_channel_id": None,
            "whitelist": [],            # 除外ユーザーID
        })

    def _save_config(self):
        save_json(DATA_FILE, self.config)

    # ==============================
    # 自動バックアップ（30分ごと）
    # ==============================

    @tasks.loop(minutes=30)
    async def auto_backup(self):
        backups = load_json(BACKUP_FILE, {})
        for guild in self.bot.guilds:
            cfg = self._guild_cfg(guild.id)
            if not cfg.get("enabled"):
                continue
            backups[str(guild.id)] = snapshot_guild(guild)
        save_json(BACKUP_FILE, backups)

    @auto_backup.before_loop
    async def before_backup(self):
        await self.bot.wait_until_ready()

    # ==============================
    # 検知ロジック
    # ==============================

    def _record_delete(self, guild_id: int, kind: str) -> int:
        """削除を記録し、現在のウィンドウ内の件数を返す"""
        cfg    = self._guild_cfg(guild_id)
        window = cfg.get("window", DEFAULT_WINDOW)
        now    = time.time()

        queue = self._delete_times[guild_id][kind]
        queue.append(now)
        # 古いものを除去
        self._delete_times[guild_id][kind] = [t for t in queue if now - t <= window]
        return len(self._delete_times[guild_id][kind])

    async def _punish_and_recover(self, guild: discord.Guild, executor: discord.Member | None, kind: str, deleted_id: int):
        """BANまたはKICKして復旧する"""
        cfg = self._guild_cfg(guild.id)

        # --- ログ ---
        log_ch_id = cfg.get("log_channel_id")
        log_ch    = guild.get_channel(log_ch_id) if log_ch_id else None

        # --- 実行者を処罰 ---
        if executor and not executor.bot:
            # ホワイトリスト
            if executor.id not in cfg.get("whitelist", []):
                action = cfg.get("action", "ban")
                try:
                    if action == "ban":
                        await guild.ban(executor, reason="[AntiRaid] 大量削除を検知")
                        action_text = "BAN"
                    else:
                        await executor.kick(reason="[AntiRaid] 大量削除を検知")
                        action_text = "KICK"

                    if log_ch:
                        embed = discord.Embed(
                            title=f"🚨 荒らし検知 → {action_text}",
                            color=discord.Color.red(),
                            timestamp=discord.utils.utcnow()
                        )
                        embed.add_field(name="実行者", value=f"{executor} (`{executor.id}`)", inline=False)
                        embed.add_field(name="削除種別", value=kind, inline=True)
                        await log_ch.send(embed=embed)
                except discord.Forbidden:
                    if log_ch:
                        await log_ch.send(f"⚠️ `{executor}` への処罰に失敗しました（権限不足）")

        # --- 復旧 ---
        if guild.id not in self._recovering:
            self._recovering.add(guild.id)
            try:
                await self._restore_guild(guild, log_ch)
            finally:
                self._recovering.discard(guild.id)

    async def _restore_guild(self, guild: discord.Guild, log_ch: Optional[discord.TextChannel]):
        """バックアップからサーバー構造を復元する"""
        backups = load_json(BACKUP_FILE, {})
        snap    = backups.get(str(guild.id))
        if not snap:
            if log_ch:
                await log_ch.send("⚠️ バックアップが見つかりません。`/antiraid_backup` を実行してください。")
            return

        restored = {"roles": 0, "categories": 0, "channels": 0}

        # --- ロール復旧 ---
        existing_role_ids = {r.id for r in guild.roles}
        # positionでソートして下から作り直す
        for rdata in sorted(snap["roles"], key=lambda x: x["position"]):
            if rdata["id"] not in existing_role_ids:
                try:
                    await guild.create_role(
                        name=rdata["name"],
                        color=discord.Color(rdata["color"]),
                        hoist=rdata["hoist"],
                        mentionable=rdata["mentionable"],
                        permissions=discord.Permissions(rdata["permissions"]),
                        reason="[AntiRaid] ロール復旧"
                    )
                    restored["roles"] += 1
                    await asyncio.sleep(0.5)
                except Exception as e:
                    print(f"[AntiRaid] ロール復旧失敗 {rdata['name']}: {e}")

        # ロールが揃ったので再取得
        await asyncio.sleep(1)

        # --- カテゴリ復旧 ---
        existing_cat_ids = {c.id for c in guild.categories}
        # カテゴリIDの新旧マッピング（復旧後にチャンネルのcategory_idを更新するため）
        cat_id_map: dict[int, int] = {}

        for cdata in sorted(snap["categories"], key=lambda x: x["position"]):
            if cdata["id"] not in existing_cat_ids:
                try:
                    overwrites = dict_to_overwrites(guild, cdata["overwrites"])
                    new_cat = await guild.create_category(
                        name=cdata["name"],
                        overwrites=overwrites,
                        reason="[AntiRaid] カテゴリ復旧"
                    )
                    cat_id_map[cdata["id"]] = new_cat.id
                    restored["categories"] += 1
                    await asyncio.sleep(0.5)
                except Exception as e:
                    print(f"[AntiRaid] カテゴリ復旧失敗 {cdata['name']}: {e}")
            else:
                cat_id_map[cdata["id"]] = cdata["id"]

        # --- チャンネル復旧 ---
        existing_ch_ids = {c.id for c in guild.channels}

        for chdata in sorted(snap["channels"], key=lambda x: x["position"]):
            if chdata["id"] not in existing_ch_ids:
                try:
                    overwrites  = dict_to_overwrites(guild, chdata["overwrites"])
                    orig_cat_id = chdata.get("category_id")
                    # 復旧済みカテゴリを優先、なければオリジナルIDで検索
                    resolved_cat_id = cat_id_map.get(orig_cat_id, orig_cat_id)
                    category = guild.get_channel(resolved_cat_id) if resolved_cat_id else None

                    ch_type = chdata["type"]

                    if ch_type == "text":
                        await guild.create_text_channel(
                            name=chdata["name"],
                            category=category,
                            topic=chdata.get("topic"),
                            nsfw=chdata.get("nsfw", False),
                            slowmode_delay=chdata.get("slowmode_delay", 0),
                            overwrites=overwrites,
                            reason="[AntiRaid] チャンネル復旧"
                        )
                    elif ch_type == "voice":
                        await guild.create_voice_channel(
                            name=chdata["name"],
                            category=category,
                            bitrate=chdata.get("bitrate", 64000),
                            user_limit=chdata.get("user_limit", 0),
                            overwrites=overwrites,
                            reason="[AntiRaid] チャンネル復旧"
                        )
                    elif ch_type == "stage_voice":
                        await guild.create_stage_channel(
                            name=chdata["name"],
                            category=category,
                            overwrites=overwrites,
                            reason="[AntiRaid] チャンネル復旧"
                        )
                    elif ch_type == "forum":
                        await guild.create_forum(
                            name=chdata["name"],
                            category=category,
                            overwrites=overwrites,
                            reason="[AntiRaid] チャンネル復旧"
                        )

                    restored["channels"] += 1
                    await asyncio.sleep(0.5)

                except Exception as e:
                    print(f"[AntiRaid] チャンネル復旧失敗 {chdata['name']}: {e}")

        # --- 結果ログ ---
        if log_ch:
            embed = discord.Embed(
                title="✅ サーバー構造の復旧完了",
                color=discord.Color.green(),
                timestamp=discord.utils.utcnow()
            )
            embed.add_field(name="ロール",     value=f"{restored['roles']}個復旧",     inline=True)
            embed.add_field(name="カテゴリ",   value=f"{restored['categories']}個復旧", inline=True)
            embed.add_field(name="チャンネル", value=f"{restored['channels']}個復旧",   inline=True)
            await log_ch.send(embed=embed)

    # ==============================
    # イベントリスナー
    # ==============================

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel: discord.abc.GuildChannel):
        guild = channel.guild
        cfg   = self._guild_cfg(guild.id)
        if not cfg.get("enabled"):
            return

        count = self._record_delete(guild.id, "channel")
        if count >= cfg.get("threshold", DEFAULT_THRESHOLD):
            # 監査ログから削除者を特定
            executor = await self._get_executor(guild, discord.AuditLogAction.channel_delete)
            await self._punish_and_recover(guild, executor, "チャンネル大量削除", channel.id)

    @commands.Cog.listener()
    async def on_guild_channel_create(self, channel: discord.abc.GuildChannel):
        """チャンネル作成時もバックアップを更新"""
        guild = channel.guild
        cfg   = self._guild_cfg(guild.id)
        if cfg.get("enabled"):
            await self._update_backup(guild)

    @commands.Cog.listener()
    async def on_guild_role_delete(self, role: discord.Role):
        guild = role.guild
        cfg   = self._guild_cfg(guild.id)
        if not cfg.get("enabled"):
            return

        count = self._record_delete(guild.id, "role")
        if count >= cfg.get("threshold", DEFAULT_THRESHOLD):
            executor = await self._get_executor(guild, discord.AuditLogAction.role_delete)
            await self._punish_and_recover(guild, executor, "ロール大量削除", role.id)

    @commands.Cog.listener()
    async def on_guild_role_create(self, role: discord.Role):
        guild = role.guild
        cfg   = self._guild_cfg(guild.id)
        if cfg.get("enabled"):
            await self._update_backup(guild)

    @commands.Cog.listener()
    async def on_member_ban(self, guild: discord.Guild, user: discord.User):
        """大量BANも検知"""
        cfg = self._guild_cfg(guild.id)
        if not cfg.get("enabled"):
            return

        count = self._record_delete(guild.id, "ban")
        if count >= cfg.get("threshold", DEFAULT_THRESHOLD):
            executor = await self._get_executor(guild, discord.AuditLogAction.ban)
            if executor and executor.id != self.bot.user.id:
                if executor.id not in cfg.get("whitelist", []):
                    try:
                        await guild.ban(executor, reason="[AntiRaid] 大量BANを検知")
                        log_ch_id = cfg.get("log_channel_id")
                        if log_ch_id:
                            log_ch = guild.get_channel(log_ch_id)
                            if log_ch:
                                await log_ch.send(f"🚨 大量BAN実行者 `{executor}` をBANしました。")
                    except discord.Forbidden:
                        pass

    @commands.Cog.listener()
    async def on_webhooks_update(self, channel: discord.abc.GuildChannel):
        """Webhook削除検知→バックアップ更新"""
        cfg = self._guild_cfg(channel.guild.id)
        if cfg.get("enabled"):
            await self._update_backup(channel.guild)

    # ==============================
    # ヘルパー
    # ==============================

    async def _get_executor(self, guild: discord.Guild, action: discord.AuditLogAction) -> Optional[discord.Member]:
        """監査ログから直近の実行者を取得"""
        try:
            async for entry in guild.audit_logs(limit=1, action=action):
                if entry.user and entry.user.id != self.bot.user.id:
                    return guild.get_member(entry.user.id)
        except discord.Forbidden:
            pass
        return None

    async def _update_backup(self, guild: discord.Guild):
        """バックアップを即時更新"""
        backups = load_json(BACKUP_FILE, {})
        backups[str(guild.id)] = snapshot_guild(guild)
        save_json(BACKUP_FILE, backups)

    # ==============================
    # スラッシュコマンド群
    # ==============================

    @app_commands.command(name="antiraid_setup", description="荒らし対策を設定します")
    @app_commands.describe(
        enabled="有効/無効",
        action="処罰方法 (ban / kick)",
        threshold="何件削除で発動するか",
        window="検知ウィンドウ（秒）",
        log_channel="ログチャンネル"
    )
    @app_commands.choices(action=[
        app_commands.Choice(name="BAN", value="ban"),
        app_commands.Choice(name="KICK", value="kick"),
    ])
    @app_commands.checks.has_permissions(administrator=True)
    async def antiraid_setup(
        self,
        interaction: discord.Interaction,
        enabled: bool,
        action: str = "ban",
        threshold: int = DEFAULT_THRESHOLD,
        window: int = DEFAULT_WINDOW,
        log_channel: Optional[discord.TextChannel] = None
    ):
        cfg = self._guild_cfg(interaction.guild.id)
        cfg["enabled"]       = enabled
        cfg["action"]        = action
        cfg["threshold"]     = max(1, threshold)
        cfg["window"]        = max(1, window)
        cfg["log_channel_id"] = log_channel.id if log_channel else cfg.get("log_channel_id")
        self._save_config()

        embed = discord.Embed(
            title="✅ AntiRaid 設定完了",
            color=discord.Color.green() if enabled else discord.Color.greyple()
        )
        embed.add_field(name="状態",         value="有効" if enabled else "無効", inline=True)
        embed.add_field(name="処罰方法",     value=action.upper(),               inline=True)
        embed.add_field(name="検知閾値",     value=f"{threshold}件 / {window}秒", inline=True)
        embed.add_field(name="ログチャンネル", value=log_channel.mention if log_channel else "未設定", inline=False)
        embed.set_footer(text="Developer @m_shoppp")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="antiraid_backup", description="今すぐサーバー構造をバックアップします")
    @app_commands.checks.has_permissions(administrator=True)
    async def antiraid_backup(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        await self._update_backup(interaction.guild)

        snap = load_json(BACKUP_FILE, {}).get(str(interaction.guild.id), {})
        embed = discord.Embed(
            title="📦 バックアップ完了",
            color=discord.Color.blue(),
            timestamp=discord.utils.utcnow()
        )
        embed.add_field(name="ロール数",       value=len(snap.get("roles", [])),      inline=True)
        embed.add_field(name="カテゴリ数",     value=len(snap.get("categories", [])), inline=True)
        embed.add_field(name="チャンネル数",   value=len(snap.get("channels", [])),   inline=True)
        embed.set_footer(text="Developer @m_shoppp")
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(name="antiraid_restore", description="バックアップからサーバー構造を手動復旧します")
    @app_commands.checks.has_permissions(administrator=True)
    async def antiraid_restore(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        backups = load_json(BACKUP_FILE, {})
        if str(interaction.guild.id) not in backups:
            return await interaction.followup.send("⚠️ バックアップが見つかりません。先に `/antiraid_backup` を実行してください。", ephemeral=True)

        if interaction.guild.id in self._recovering:
            return await interaction.followup.send("⏳ 現在復旧中です。しばらくお待ちください。", ephemeral=True)

        cfg = self._guild_cfg(interaction.guild.id)
        log_ch_id = cfg.get("log_channel_id")
        log_ch    = interaction.guild.get_channel(log_ch_id) if log_ch_id else None

        await interaction.followup.send("🔄 復旧を開始します...", ephemeral=True)

        self._recovering.add(interaction.guild.id)
        try:
            await self._restore_guild(interaction.guild, log_ch or interaction.channel)
        finally:
            self._recovering.discard(interaction.guild.id)

    @app_commands.command(name="antiraid_whitelist_add", description="ホワイトリストにユーザーを追加します（処罰対象外）")
    @app_commands.describe(user="追加するユーザー")
    @app_commands.checks.has_permissions(administrator=True)
    async def antiraid_whitelist_add(self, interaction: discord.Interaction, user: discord.User):
        cfg = self._guild_cfg(interaction.guild.id)
        wl  = cfg.setdefault("whitelist", [])
        if user.id not in wl:
            wl.append(user.id)
            self._save_config()
            await interaction.response.send_message(f"✅ {user.mention} をホワイトリストに追加しました。", ephemeral=True)
        else:
            await interaction.response.send_message(f"🚫 {user.mention} は既にホワイトリストにいます。", ephemeral=True)

    @app_commands.command(name="antiraid_whitelist_remove", description="ホワイトリストからユーザーを削除します")
    @app_commands.describe(user="削除するユーザー")
    @app_commands.checks.has_permissions(administrator=True)
    async def antiraid_whitelist_remove(self, interaction: discord.Interaction, user: discord.User):
        cfg = self._guild_cfg(interaction.guild.id)
        wl  = cfg.setdefault("whitelist", [])
        if user.id in wl:
            wl.remove(user.id)
            self._save_config()
            await interaction.response.send_message(f"✅ {user.mention} をホワイトリストから削除しました。", ephemeral=True)
        else:
            await interaction.response.send_message(f"🚫 {user.mention} はホワイトリストにいません。", ephemeral=True)

    @app_commands.command(name="antiraid_status", description="現在のAntiRaid設定を確認します")
    @app_commands.checks.has_permissions(administrator=True)
    async def antiraid_status(self, interaction: discord.Interaction):
        cfg = self._guild_cfg(interaction.guild.id)
        wl  = cfg.get("whitelist", [])
        wl_mentions = [f"<@{uid}>" for uid in wl] if wl else ["なし"]

        backups = load_json(BACKUP_FILE, {})
        snap    = backups.get(str(interaction.guild.id))
        backup_time = (
            f"<t:{int(snap['timestamp'])}:R>" if snap else "バックアップなし"
        )

        log_ch_id = cfg.get("log_channel_id")
        log_ch_mention = f"<#{log_ch_id}>" if log_ch_id else "未設定"

        embed = discord.Embed(
            title="🛡️ AntiRaid ステータス",
            color=discord.Color.green() if cfg.get("enabled") else discord.Color.greyple(),
            timestamp=discord.utils.utcnow()
        )
        embed.add_field(name="状態",           value="✅ 有効" if cfg.get("enabled") else "❌ 無効", inline=True)
        embed.add_field(name="処罰方法",       value=cfg.get("action", "ban").upper(),                 inline=True)
        embed.add_field(name="検知閾値",       value=f"{cfg.get('threshold', DEFAULT_THRESHOLD)}件 / {cfg.get('window', DEFAULT_WINDOW)}秒", inline=True)
        embed.add_field(name="ログチャンネル", value=log_ch_mention,                                    inline=True)
        embed.add_field(name="最終バックアップ", value=backup_time,                                    inline=True)
        embed.add_field(name="ホワイトリスト", value=", ".join(wl_mentions),                           inline=False)
        embed.set_footer(text="Developer @m_shoppp")
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(AntiRaid(bot))
