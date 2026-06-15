"""
antiraid.py - 荒らし対策 & 自動復旧システム（詳細設定パネルUI版）

コマンド:
  /antiraid_panel    → 荒らし検知の詳細設定パネルを設置
  /antiraid_recovery → 復旧パネルを設置
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

# ──────────────────────────────
# 定数
# ──────────────────────────────

DATA_FILE   = "antiraid_data.json"
BACKUP_FILE = "server_backup.json"

DEFAULTS = {
    "enabled":              False,
    # 処罰
    "action":               "ban",       # ban / kick / timeout
    "timeout_minutes":      10,
    # 検知 ON/OFF
    "detect_channel_del":   True,
    "detect_role_del":      True,
    "detect_mass_ban":      True,
    # チャンネル削除検知
    "ch_threshold":         3,
    "ch_window":            10,
    # ロール削除検知
    "role_threshold":       3,
    "role_window":          10,
    # 大量BAN検知
    "ban_threshold":        3,
    "ban_window":           10,
    # 自動復旧
    "auto_restore":         True,
    # ログ
    "log_channel_id":       None,
    # ホワイトリスト
    "whitelist":            [],
}

# ──────────────────────────────
# JSON ユーティリティ
# ──────────────────────────────

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

# ──────────────────────────────
# Permissions シリアライズ
# ──────────────────────────────

def overwrites_to_dict(overwrites: dict) -> list:
    result = []
    for target, perm in overwrites.items():
        allow, deny = perm.pair()
        entry = {"allow": allow.value, "deny": deny.value}
        if isinstance(target, discord.Role):
            entry["type"] = "role"
            entry["id"]   = target.id
            entry["name"] = target.name
        elif isinstance(target, discord.Member):
            entry["type"] = "member"
            entry["id"]   = target.id
            entry["name"] = str(target)
        result.append(entry)
    return result

def dict_to_overwrites(guild: discord.Guild, data: list) -> dict:
    result = {}
    for entry in data:
        allow  = discord.Permissions(entry["allow"])
        deny   = discord.Permissions(entry["deny"])
        perm   = discord.PermissionOverwrite.from_pair(allow, deny)
        if entry["type"] == "role":
            target = guild.get_role(entry["id"]) or discord.utils.get(guild.roles, name=entry["name"])
        else:
            target = guild.get_member(entry["id"])
        if target:
            result[target] = perm
    return result

# ──────────────────────────────
# スナップショット
# ──────────────────────────────

def snapshot_guild(guild: discord.Guild) -> dict:
    snap = {
        "guild_id":  guild.id,
        "timestamp": time.time(),
        "roles":      [],
        "categories": [],
        "channels":   [],
    }
    for role in guild.roles:
        if role.is_default():
            continue
        snap["roles"].append({
            "id": role.id, "name": role.name,
            "color": role.color.value, "hoist": role.hoist,
            "mentionable": role.mentionable,
            "permissions": role.permissions.value,
            "position": role.position,
        })
    for cat in guild.categories:
        snap["categories"].append({
            "id": cat.id, "name": cat.name,
            "position": cat.position,
            "overwrites": overwrites_to_dict(cat.overwrites),
        })
    for ch in guild.channels:
        if isinstance(ch, discord.CategoryChannel):
            continue
        entry = {
            "id": ch.id, "name": ch.name,
            "type": str(ch.type),
            "position": ch.position,
            "category_id": ch.category_id,
            "overwrites": overwrites_to_dict(ch.overwrites),
            "topic": None, "nsfw": False,
            "slowmode_delay": 0, "bitrate": None, "user_limit": None,
        }
        if isinstance(ch, discord.TextChannel):
            entry["topic"]          = ch.topic
            entry["nsfw"]           = ch.nsfw
            entry["slowmode_delay"] = ch.slowmode_delay
        elif isinstance(ch, discord.VoiceChannel):
            entry["bitrate"]    = ch.bitrate
            entry["user_limit"] = ch.user_limit
        snap["channels"].append(entry)
    return snap

# ──────────────────────────────
# 設定ステータス Embed 生成（共通）
# ──────────────────────────────

def build_status_embed(cfg: dict) -> discord.Embed:
    on  = "✅ ON"
    off = "❌ OFF"

    action = cfg.get("action", "ban")
    if action == "timeout":
        action_text = f"タイムアウト（{cfg.get('timeout_minutes', 10)}分）"
    else:
        action_text = action.upper()

    wl      = cfg.get("whitelist", [])
    wl_text = ", ".join(f"<@{uid}>" for uid in wl) if wl else "なし"

    log_id  = cfg.get("log_channel_id")
    log_text = f"<#{log_id}>" if log_id else "未設定"

    backups = load_json(BACKUP_FILE, {})
    snap    = backups.get(str(cfg.get("_guild_id", "")))
    backup_ts = f"<t:{int(snap['timestamp'])}:R>" if snap else "なし"

    embed = discord.Embed(
        title="🛡️ AntiRaid 現在の設定",
        color=discord.Color.green() if cfg.get("enabled") else discord.Color.greyple(),
        timestamp=discord.utils.utcnow()
    )
    embed.add_field(name="荒らし対策",     value=on if cfg.get("enabled") else off, inline=True)
    embed.add_field(name="処罰方法",       value=action_text,                        inline=True)
    embed.add_field(name="自動復旧",       value=on if cfg.get("auto_restore", True) else off, inline=True)
    embed.add_field(name="ログチャンネル", value=log_text,                            inline=True)
    embed.add_field(name="最終バックアップ", value=backup_ts,                         inline=True)
    embed.add_field(name="\u200b",         value="\u200b",                            inline=True)

    embed.add_field(
        name="📁 チャンネル削除検知",
        value=(
            f"{on if cfg.get('detect_channel_del', True) else off}\n"
            f"閾値: **{cfg.get('ch_threshold', 3)}件 / {cfg.get('ch_window', 10)}秒**"
        ),
        inline=True
    )
    embed.add_field(
        name="🎭 ロール削除検知",
        value=(
            f"{on if cfg.get('detect_role_del', True) else off}\n"
            f"閾値: **{cfg.get('role_threshold', 3)}件 / {cfg.get('role_window', 10)}秒**"
        ),
        inline=True
    )
    embed.add_field(
        name="🔨 大量BAN検知",
        value=(
            f"{on if cfg.get('detect_mass_ban', True) else off}\n"
            f"閾値: **{cfg.get('ban_threshold', 3)}件 / {cfg.get('ban_window', 10)}秒**"
        ),
        inline=True
    )
    embed.add_field(name="ホワイトリスト", value=wl_text, inline=False)
    embed.set_footer(text="Developer @yuzu09591")
    return embed

# ──────────────────────────────
# モーダル群
# ──────────────────────────────

class ChannelDetectModal(discord.ui.Modal, title="📁 チャンネル削除検知の設定"):
    threshold = discord.ui.TextInput(label="発動件数（何件削除で検知するか）", placeholder="例: 3", min_length=1, max_length=3)
    window    = discord.ui.TextInput(label="検知ウィンドウ（秒）",             placeholder="例: 10", min_length=1, max_length=4)

    def __init__(self, cog: "AntiRaid"):
        super().__init__()
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction):
        cfg = self.cog._guild_cfg(interaction.guild.id)
        try:
            cfg["ch_threshold"] = max(1, int(self.threshold.value))
            cfg["ch_window"]    = max(1, int(self.window.value))
        except ValueError:
            return await interaction.response.send_message("⚠️ 数字を入力してください。", ephemeral=True)
        self.cog._save_config()
        await interaction.response.send_message(
            f"✅ チャンネル削除検知を更新しました。\n閾値: **{cfg['ch_threshold']}件 / {cfg['ch_window']}秒**",
            ephemeral=True
        )


class RoleDetectModal(discord.ui.Modal, title="🎭 ロール削除検知の設定"):
    threshold = discord.ui.TextInput(label="発動件数（何件削除で検知するか）", placeholder="例: 3", min_length=1, max_length=3)
    window    = discord.ui.TextInput(label="検知ウィンドウ（秒）",             placeholder="例: 10", min_length=1, max_length=4)

    def __init__(self, cog: "AntiRaid"):
        super().__init__()
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction):
        cfg = self.cog._guild_cfg(interaction.guild.id)
        try:
            cfg["role_threshold"] = max(1, int(self.threshold.value))
            cfg["role_window"]    = max(1, int(self.window.value))
        except ValueError:
            return await interaction.response.send_message("⚠️ 数字を入力してください。", ephemeral=True)
        self.cog._save_config()
        await interaction.response.send_message(
            f"✅ ロール削除検知を更新しました。\n閾値: **{cfg['role_threshold']}件 / {cfg['role_window']}秒**",
            ephemeral=True
        )


class BanDetectModal(discord.ui.Modal, title="🔨 大量BAN検知の設定"):
    threshold = discord.ui.TextInput(label="発動件数（何件BANで検知するか）", placeholder="例: 3", min_length=1, max_length=3)
    window    = discord.ui.TextInput(label="検知ウィンドウ（秒）",            placeholder="例: 10", min_length=1, max_length=4)

    def __init__(self, cog: "AntiRaid"):
        super().__init__()
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction):
        cfg = self.cog._guild_cfg(interaction.guild.id)
        try:
            cfg["ban_threshold"] = max(1, int(self.threshold.value))
            cfg["ban_window"]    = max(1, int(self.window.value))
        except ValueError:
            return await interaction.response.send_message("⚠️ 数字を入力してください。", ephemeral=True)
        self.cog._save_config()
        await interaction.response.send_message(
            f"✅ 大量BAN検知を更新しました。\n閾値: **{cfg['ban_threshold']}件 / {cfg['ban_window']}秒**",
            ephemeral=True
        )


class TimeoutModal(discord.ui.Modal, title="⏱️ タイムアウト時間の設定"):
    minutes = discord.ui.TextInput(label="タイムアウト時間（分）", placeholder="例: 10  ※最大40320分（28日）", min_length=1, max_length=5)

    def __init__(self, cog: "AntiRaid"):
        super().__init__()
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction):
        cfg = self.cog._guild_cfg(interaction.guild.id)
        try:
            minutes = max(1, min(40320, int(self.minutes.value)))
        except ValueError:
            return await interaction.response.send_message("⚠️ 数字を入力してください。", ephemeral=True)
        cfg["timeout_minutes"] = minutes
        cfg["action"]          = "timeout"
        self.cog._save_config()
        await interaction.response.send_message(
            f"✅ 処罰をタイムアウト（**{minutes}分**）に設定しました。",
            ephemeral=True
        )


class LogChannelModal(discord.ui.Modal, title="📋 ログチャンネルの設定"):
    channel_id = discord.ui.TextInput(
        label="ログチャンネルID（空欄で現在のチャンネル）",
        placeholder="例: 1234567890123456789",
        required=False,
    )

    def __init__(self, cog: "AntiRaid"):
        super().__init__()
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction):
        cfg    = self.cog._guild_cfg(interaction.guild.id)
        raw_id = self.channel_id.value.strip()
        if raw_id:
            try:
                ch_id = int(raw_id)
                ch    = interaction.guild.get_channel(ch_id)
                if not ch:
                    return await interaction.response.send_message("⚠️ そのIDのチャンネルが見つかりません。", ephemeral=True)
                cfg["log_channel_id"] = ch_id
            except ValueError:
                return await interaction.response.send_message("⚠️ チャンネルIDは数字で入力してください。", ephemeral=True)
        else:
            cfg["log_channel_id"] = interaction.channel.id
        self.cog._save_config()
        log_text = f"<#{cfg['log_channel_id']}>"
        await interaction.response.send_message(f"✅ ログチャンネルを {log_text} に設定しました。", ephemeral=True)


class WhitelistAddModal(discord.ui.Modal, title="➕ ホワイトリストに追加"):
    user_id = discord.ui.TextInput(label="ユーザーID", placeholder="例: 1234567890123456789", min_length=17, max_length=20)

    def __init__(self, cog: "AntiRaid"):
        super().__init__()
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction):
        cfg = self.cog._guild_cfg(interaction.guild.id)
        try:
            uid = int(self.user_id.value.strip())
        except ValueError:
            return await interaction.response.send_message("⚠️ ユーザーIDは数字で入力してください。", ephemeral=True)
        wl = cfg.setdefault("whitelist", [])
        if uid in wl:
            return await interaction.response.send_message(f"🚫 <@{uid}> は既にホワイトリストにいます。", ephemeral=True)
        wl.append(uid)
        self.cog._save_config()
        await interaction.response.send_message(f"✅ <@{uid}> をホワイトリストに追加しました。", ephemeral=True)


class WhitelistRemoveModal(discord.ui.Modal, title="➖ ホワイトリストから削除"):
    user_id = discord.ui.TextInput(label="ユーザーID", placeholder="例: 1234567890123456789", min_length=17, max_length=20)

    def __init__(self, cog: "AntiRaid"):
        super().__init__()
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction):
        cfg = self.cog._guild_cfg(interaction.guild.id)
        try:
            uid = int(self.user_id.value.strip())
        except ValueError:
            return await interaction.response.send_message("⚠️ ユーザーIDは数字で入力してください。", ephemeral=True)
        wl = cfg.setdefault("whitelist", [])
        if uid not in wl:
            return await interaction.response.send_message(f"🚫 <@{uid}> はホワイトリストにいません。", ephemeral=True)
        wl.remove(uid)
        self.cog._save_config()
        await interaction.response.send_message(f"✅ <@{uid}> をホワイトリストから削除しました。", ephemeral=True)

# ──────────────────────────────
# 設定パネル View（メイン）
# ──────────────────────────────

class AntiRaidPanelView(discord.ui.View):
    """/antiraid_panel で設置する常駐パネル"""

    def __init__(self, cog: "AntiRaid"):
        super().__init__(timeout=None)
        self.cog = cog

    def _check_admin(self, interaction: discord.Interaction) -> bool:
        return interaction.user.guild_permissions.administrator

    # ── Row 0: 有効/無効 ──────────────────────────────
    @discord.ui.button(label="✅ 有効にする", style=discord.ButtonStyle.success, custom_id="ar_enable", row=0)
    async def enable(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self._check_admin(interaction):
            return await interaction.response.send_message("🚫 管理者のみ操作できます。", ephemeral=True)
        cfg = self.cog._guild_cfg(interaction.guild.id)
        cfg["enabled"] = True
        self.cog._save_config()
        await interaction.response.send_message("✅ 荒らし対策を **有効** にしました。", ephemeral=True)

    @discord.ui.button(label="❌ 無効にする", style=discord.ButtonStyle.danger, custom_id="ar_disable", row=0)
    async def disable(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self._check_admin(interaction):
            return await interaction.response.send_message("🚫 管理者のみ操作できます。", ephemeral=True)
        cfg = self.cog._guild_cfg(interaction.guild.id)
        cfg["enabled"] = False
        self.cog._save_config()
        await interaction.response.send_message("❌ 荒らし対策を **無効** にしました。", ephemeral=True)

    @discord.ui.button(label="🛡️ 現在の設定を確認", style=discord.ButtonStyle.secondary, custom_id="ar_status", row=0)
    async def status(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self._check_admin(interaction):
            return await interaction.response.send_message("🚫 管理者のみ操作できます。", ephemeral=True)
        cfg = self.cog._guild_cfg(interaction.guild.id)
        cfg["_guild_id"] = str(interaction.guild.id)
        await interaction.response.send_message(embed=build_status_embed(cfg), ephemeral=True)

    # ── Row 1: 処罰方法 ───────────────────────────────
    @discord.ui.button(label="🔨 処罰: BAN", style=discord.ButtonStyle.primary, custom_id="ar_action_ban", row=1)
    async def action_ban(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self._check_admin(interaction):
            return await interaction.response.send_message("🚫 管理者のみ操作できます。", ephemeral=True)
        cfg = self.cog._guild_cfg(interaction.guild.id)
        cfg["action"] = "ban"
        self.cog._save_config()
        await interaction.response.send_message("🔨 処罰方法を **BAN** に設定しました。", ephemeral=True)

    @discord.ui.button(label="👢 処罰: KICK", style=discord.ButtonStyle.primary, custom_id="ar_action_kick", row=1)
    async def action_kick(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self._check_admin(interaction):
            return await interaction.response.send_message("🚫 管理者のみ操作できます。", ephemeral=True)
        cfg = self.cog._guild_cfg(interaction.guild.id)
        cfg["action"] = "kick"
        self.cog._save_config()
        await interaction.response.send_message("👢 処罰方法を **KICK** に設定しました。", ephemeral=True)

    @discord.ui.button(label="⏱️ 処罰: タイムアウト（時間設定）", style=discord.ButtonStyle.primary, custom_id="ar_action_timeout", row=1)
    async def action_timeout(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self._check_admin(interaction):
            return await interaction.response.send_message("🚫 管理者のみ操作できます。", ephemeral=True)
        await interaction.response.send_modal(TimeoutModal(self.cog))

    # ── Row 2: 検知ON/OFF ────────────────────────────
    @discord.ui.button(label="📁 チャンネル削除検知 切替", style=discord.ButtonStyle.secondary, custom_id="ar_toggle_ch", row=2)
    async def toggle_ch(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self._check_admin(interaction):
            return await interaction.response.send_message("🚫 管理者のみ操作できます。", ephemeral=True)
        cfg     = self.cog._guild_cfg(interaction.guild.id)
        new_val = not cfg.get("detect_channel_del", True)
        cfg["detect_channel_del"] = new_val
        self.cog._save_config()
        state = "✅ ON" if new_val else "❌ OFF"
        await interaction.response.send_message(f"📁 チャンネル削除検知を **{state}** にしました。", ephemeral=True)

    @discord.ui.button(label="🎭 ロール削除検知 切替", style=discord.ButtonStyle.secondary, custom_id="ar_toggle_role", row=2)
    async def toggle_role(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self._check_admin(interaction):
            return await interaction.response.send_message("🚫 管理者のみ操作できます。", ephemeral=True)
        cfg     = self.cog._guild_cfg(interaction.guild.id)
        new_val = not cfg.get("detect_role_del", True)
        cfg["detect_role_del"] = new_val
        self.cog._save_config()
        state = "✅ ON" if new_val else "❌ OFF"
        await interaction.response.send_message(f"🎭 ロール削除検知を **{state}** にしました。", ephemeral=True)

    @discord.ui.button(label="🔨 大量BAN検知 切替", style=discord.ButtonStyle.secondary, custom_id="ar_toggle_ban", row=2)
    async def toggle_ban(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self._check_admin(interaction):
            return await interaction.response.send_message("🚫 管理者のみ操作できます。", ephemeral=True)
        cfg     = self.cog._guild_cfg(interaction.guild.id)
        new_val = not cfg.get("detect_mass_ban", True)
        cfg["detect_mass_ban"] = new_val
        self.cog._save_config()
        state = "✅ ON" if new_val else "❌ OFF"
        await interaction.response.send_message(f"🔨 大量BAN検知を **{state}** にしました。", ephemeral=True)

    # ── Row 3: 閾値設定（各種モーダル） ─────────────
    @discord.ui.button(label="⚙️ チャンネル削除 閾値設定", style=discord.ButtonStyle.secondary, custom_id="ar_cfg_ch", row=3)
    async def cfg_ch(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self._check_admin(interaction):
            return await interaction.response.send_message("🚫 管理者のみ操作できます。", ephemeral=True)
        await interaction.response.send_modal(ChannelDetectModal(self.cog))

    @discord.ui.button(label="⚙️ ロール削除 閾値設定", style=discord.ButtonStyle.secondary, custom_id="ar_cfg_role", row=3)
    async def cfg_role(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self._check_admin(interaction):
            return await interaction.response.send_message("🚫 管理者のみ操作できます。", ephemeral=True)
        await interaction.response.send_modal(RoleDetectModal(self.cog))

    @discord.ui.button(label="⚙️ 大量BAN 閾値設定", style=discord.ButtonStyle.secondary, custom_id="ar_cfg_ban", row=3)
    async def cfg_ban(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self._check_admin(interaction):
            return await interaction.response.send_message("🚫 管理者のみ操作できます。", ephemeral=True)
        await interaction.response.send_modal(BanDetectModal(self.cog))

    # ── Row 4: ログ・自動復旧・ホワイトリスト ────────
    @discord.ui.button(label="📋 ログチャンネル設定", style=discord.ButtonStyle.secondary, custom_id="ar_logch", row=4)
    async def log_ch(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self._check_admin(interaction):
            return await interaction.response.send_message("🚫 管理者のみ操作できます。", ephemeral=True)
        await interaction.response.send_modal(LogChannelModal(self.cog))

    @discord.ui.button(label="🔄 自動復旧 切替", style=discord.ButtonStyle.secondary, custom_id="ar_toggle_restore", row=4)
    async def toggle_restore(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self._check_admin(interaction):
            return await interaction.response.send_message("🚫 管理者のみ操作できます。", ephemeral=True)
        cfg     = self.cog._guild_cfg(interaction.guild.id)
        new_val = not cfg.get("auto_restore", True)
        cfg["auto_restore"] = new_val
        self.cog._save_config()
        state = "✅ ON" if new_val else "❌ OFF"
        await interaction.response.send_message(f"🔄 自動復旧を **{state}** にしました。", ephemeral=True)

    @discord.ui.button(label="➕ WL追加", style=discord.ButtonStyle.success, custom_id="ar_wl_add", row=4)
    async def wl_add(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self._check_admin(interaction):
            return await interaction.response.send_message("🚫 管理者のみ操作できます。", ephemeral=True)
        await interaction.response.send_modal(WhitelistAddModal(self.cog))

    @discord.ui.button(label="➖ WL削除", style=discord.ButtonStyle.danger, custom_id="ar_wl_remove", row=4)
    async def wl_remove(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self._check_admin(interaction):
            return await interaction.response.send_message("🚫 管理者のみ操作できます。", ephemeral=True)
        await interaction.response.send_modal(WhitelistRemoveModal(self.cog))


# ──────────────────────────────
# 復旧パネル View
# ──────────────────────────────

class RecoveryPanelView(discord.ui.View):
    """/antiraid_recovery で設置する常駐パネル"""

    def __init__(self, cog: "AntiRaid"):
        super().__init__(timeout=None)
        self.cog = cog

    def _check_admin(self, interaction: discord.Interaction) -> bool:
        return interaction.user.guild_permissions.administrator

    @discord.ui.button(label="🔄 全体復旧（ロール・カテゴリ・チャンネル）", style=discord.ButtonStyle.danger, custom_id="rv_all", row=0)
    async def restore_all(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self._check_admin(interaction):
            return await interaction.response.send_message("🚫 管理者のみ操作できます。", ephemeral=True)
        await self._start_restore(interaction, mode="all")

    @discord.ui.button(label="📁 チャンネル＆カテゴリのみ復旧", style=discord.ButtonStyle.primary, custom_id="rv_channels", row=1)
    async def restore_channels(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self._check_admin(interaction):
            return await interaction.response.send_message("🚫 管理者のみ操作できます。", ephemeral=True)
        await self._start_restore(interaction, mode="channels")

    @discord.ui.button(label="🎭 ロールのみ復旧", style=discord.ButtonStyle.primary, custom_id="rv_roles", row=1)
    async def restore_roles(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self._check_admin(interaction):
            return await interaction.response.send_message("🚫 管理者のみ操作できます。", ephemeral=True)
        await self._start_restore(interaction, mode="roles")

    @discord.ui.button(label="📦 バックアップ取得", style=discord.ButtonStyle.secondary, custom_id="rv_backup", row=2)
    async def backup(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self._check_admin(interaction):
            return await interaction.response.send_message("🚫 管理者のみ操作できます。", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        await self.cog._update_backup(interaction.guild)
        snap  = load_json(BACKUP_FILE, {}).get(str(interaction.guild.id), {})
        embed = discord.Embed(title="📦 バックアップ完了", color=discord.Color.blue(), timestamp=discord.utils.utcnow())
        embed.add_field(name="ロール数",     value=len(snap.get("roles", [])),      inline=True)
        embed.add_field(name="カテゴリ数",   value=len(snap.get("categories", [])), inline=True)
        embed.add_field(name="チャンネル数", value=len(snap.get("channels", [])),   inline=True)
        embed.set_footer(text="Developer @yuzu09591")
        await interaction.followup.send(embed=embed, ephemeral=True)

    @discord.ui.button(label="📋 バックアップ情報確認", style=discord.ButtonStyle.secondary, custom_id="rv_info", row=2)
    async def backup_info(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self._check_admin(interaction):
            return await interaction.response.send_message("🚫 管理者のみ操作できます。", ephemeral=True)
        snap = load_json(BACKUP_FILE, {}).get(str(interaction.guild.id))
        if not snap:
            return await interaction.response.send_message(
                "⚠️ バックアップがありません。先に「📦 バックアップ取得」を押してください。", ephemeral=True
            )
        embed = discord.Embed(title="📋 バックアップ情報", color=discord.Color.blue(), timestamp=discord.utils.utcnow())
        embed.add_field(name="取得日時",     value=f"<t:{int(snap['timestamp'])}:F>", inline=False)
        embed.add_field(name="ロール数",     value=f"{len(snap.get('roles', []))}個",      inline=True)
        embed.add_field(name="カテゴリ数",   value=f"{len(snap.get('categories', []))}個", inline=True)
        embed.add_field(name="チャンネル数", value=f"{len(snap.get('channels', []))}個",   inline=True)
        embed.set_footer(text="Developer @yuzu09591")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    async def _start_restore(self, interaction: discord.Interaction, mode: str):
        if not load_json(BACKUP_FILE, {}).get(str(interaction.guild.id)):
            return await interaction.response.send_message(
                "⚠️ バックアップが見つかりません。先に「📦 バックアップ取得」を押してください。", ephemeral=True
            )
        if interaction.guild.id in self.cog._recovering:
            return await interaction.response.send_message("⏳ 現在復旧中です。しばらくお待ちください。", ephemeral=True)

        await interaction.response.defer(ephemeral=True)
        cfg       = self.cog._guild_cfg(interaction.guild.id)
        log_ch_id = cfg.get("log_channel_id")
        log_ch    = interaction.guild.get_channel(log_ch_id) if log_ch_id else interaction.channel
        mode_text = {"all": "全体", "channels": "チャンネル＆カテゴリ", "roles": "ロール"}.get(mode, "全体")
        await interaction.followup.send(f"🔄 **{mode_text}復旧**を開始します...", ephemeral=True)

        self.cog._recovering.add(interaction.guild.id)
        try:
            await self.cog._restore_guild(interaction.guild, log_ch, mode=mode)
        finally:
            self.cog._recovering.discard(interaction.guild.id)


# ──────────────────────────────
# Cog 本体
# ──────────────────────────────

class AntiRaid(commands.Cog):

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.config: dict     = load_json(DATA_FILE, {})
        self._delete_times    = defaultdict(lambda: defaultdict(list))
        self._recovering: set = set()
        self.auto_backup.start()

    def cog_unload(self):
        self.auto_backup.cancel()

    # ── 設定ヘルパー ─────────────────────────────────

    def _guild_cfg(self, guild_id: int) -> dict:
        cfg = self.config.setdefault(str(guild_id), {})
        for k, v in DEFAULTS.items():
            cfg.setdefault(k, v)
        return cfg

    def _save_config(self):
        save_json(DATA_FILE, self.config)

    # ── 自動バックアップ（30分ごと） ─────────────────

    @tasks.loop(minutes=30)
    async def auto_backup(self):
        backups = load_json(BACKUP_FILE, {})
        for guild in self.bot.guilds:
            if self._guild_cfg(guild.id).get("enabled"):
                backups[str(guild.id)] = snapshot_guild(guild)
        save_json(BACKUP_FILE, backups)

    @auto_backup.before_loop
    async def before_backup(self):
        await self.bot.wait_until_ready()

    # ── 削除カウント ─────────────────────────────────

    def _record_delete(self, guild_id: int, kind: str, window: int) -> int:
        now   = time.time()
        queue = self._delete_times[guild_id][kind]
        queue.append(now)
        self._delete_times[guild_id][kind] = [t for t in queue if now - t <= window]
        return len(self._delete_times[guild_id][kind])

    # ── 処罰＆復旧 ───────────────────────────────────

    async def _punish_and_recover(self, guild: discord.Guild, executor, kind: str, *, auto_restore: bool = True):
        cfg       = self._guild_cfg(guild.id)
        log_ch_id = cfg.get("log_channel_id")
        log_ch    = guild.get_channel(log_ch_id) if log_ch_id else None

        if executor and not executor.bot:
            if executor.id not in cfg.get("whitelist", []):
                action = cfg.get("action", "ban")
                try:
                    if action == "ban":
                        await guild.ban(executor, reason="[AntiRaid] 大量削除/BAN検知")
                        action_text = "BAN"
                    elif action == "kick":
                        await executor.kick(reason="[AntiRaid] 大量削除/BAN検知")
                        action_text = "KICK"
                    elif action == "timeout":
                        minutes = cfg.get("timeout_minutes", 10)
                        until   = discord.utils.utcnow() + discord.timedelta(minutes=minutes)
                        await executor.timeout(until, reason="[AntiRaid] 大量削除/BAN検知")
                        action_text = f"タイムアウト({minutes}分)"
                    else:
                        action_text = "不明"

                    if log_ch:
                        embed = discord.Embed(
                            title=f"🚨 荒らし検知 → {action_text}",
                            color=discord.Color.red(),
                            timestamp=discord.utils.utcnow()
                        )
                        embed.add_field(name="実行者",   value=f"{executor} (`{executor.id}`)", inline=False)
                        embed.add_field(name="検知種別", value=kind, inline=True)
                        await log_ch.send(embed=embed)

                except discord.Forbidden:
                    if log_ch:
                        await log_ch.send(f"⚠️ `{executor}` への処罰に失敗しました（権限不足）")

        if auto_restore and guild.id not in self._recovering:
            self._recovering.add(guild.id)
            try:
                await self._restore_guild(guild, log_ch, mode="all")
            finally:
                self._recovering.discard(guild.id)

    # ── サーバー復旧 ─────────────────────────────────

    async def _restore_guild(self, guild: discord.Guild, log_ch, *, mode: str = "all"):
        backups = load_json(BACKUP_FILE, {})
        snap    = backups.get(str(guild.id))
        if not snap:
            if log_ch:
                await log_ch.send("⚠️ バックアップが見つかりません。復旧パネルから「📦 バックアップ取得」を実行してください。")
            return

        restored = {"roles": 0, "categories": 0, "channels": 0}

        if mode in ("all", "roles"):
            existing_ids = {r.id for r in guild.roles}
            for rdata in sorted(snap["roles"], key=lambda x: x["position"]):
                if rdata["id"] not in existing_ids:
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
            await asyncio.sleep(1)

        if mode in ("all", "channels"):
            existing_cat_ids = {c.id for c in guild.categories}
            cat_id_map: dict[int, int] = {}

            for cdata in sorted(snap["categories"], key=lambda x: x["position"]):
                if cdata["id"] not in existing_cat_ids:
                    try:
                        new_cat = await guild.create_category(
                            name=cdata["name"],
                            overwrites=dict_to_overwrites(guild, cdata["overwrites"]),
                            reason="[AntiRaid] カテゴリ復旧"
                        )
                        cat_id_map[cdata["id"]] = new_cat.id
                        restored["categories"] += 1
                        await asyncio.sleep(0.5)
                    except Exception as e:
                        print(f"[AntiRaid] カテゴリ復旧失敗 {cdata['name']}: {e}")
                else:
                    cat_id_map[cdata["id"]] = cdata["id"]

            existing_ch_ids = {c.id for c in guild.channels}
            for chdata in sorted(snap["channels"], key=lambda x: x["position"]):
                if chdata["id"] not in existing_ch_ids:
                    try:
                        overwrites      = dict_to_overwrites(guild, chdata["overwrites"])
                        orig_cat_id     = chdata.get("category_id")
                        resolved_cat_id = cat_id_map.get(orig_cat_id, orig_cat_id)
                        category        = guild.get_channel(resolved_cat_id) if resolved_cat_id else None
                        ch_type         = chdata["type"]

                        if ch_type == "text":
                            await guild.create_text_channel(
                                name=chdata["name"], category=category,
                                topic=chdata.get("topic"),
                                nsfw=chdata.get("nsfw", False),
                                slowmode_delay=chdata.get("slowmode_delay", 0),
                                overwrites=overwrites, reason="[AntiRaid] チャンネル復旧"
                            )
                        elif ch_type == "voice":
                            await guild.create_voice_channel(
                                name=chdata["name"], category=category,
                                bitrate=chdata.get("bitrate", 64000),
                                user_limit=chdata.get("user_limit", 0),
                                overwrites=overwrites, reason="[AntiRaid] チャンネル復旧"
                            )
                        elif ch_type == "stage_voice":
                            await guild.create_stage_channel(
                                name=chdata["name"], category=category,
                                overwrites=overwrites, reason="[AntiRaid] チャンネル復旧"
                            )
                        elif ch_type == "forum":
                            await guild.create_forum(
                                name=chdata["name"], category=category,
                                overwrites=overwrites, reason="[AntiRaid] チャンネル復旧"
                            )

                        restored["channels"] += 1
                        await asyncio.sleep(0.5)
                    except Exception as e:
                        print(f"[AntiRaid] チャンネル復旧失敗 {chdata['name']}: {e}")

        if log_ch:
            embed = discord.Embed(title="✅ 復旧完了", color=discord.Color.green(), timestamp=discord.utils.utcnow())
            embed.add_field(name="ロール",     value=f"{restored['roles']}個復旧",     inline=True)
            embed.add_field(name="カテゴリ",   value=f"{restored['categories']}個復旧", inline=True)
            embed.add_field(name="チャンネル", value=f"{restored['channels']}個復旧",   inline=True)
            embed.set_footer(text="Developer @yuzu09591")
            await log_ch.send(embed=embed)

    # ── バックアップ更新 ──────────────────────────────

    async def _update_backup(self, guild: discord.Guild):
        backups = load_json(BACKUP_FILE, {})
        backups[str(guild.id)] = snapshot_guild(guild)
        save_json(BACKUP_FILE, backups)

    # ── 監査ログから実行者取得 ───────────────────────

    async def _get_executor(self, guild: discord.Guild, action: discord.AuditLogAction):
        try:
            async for entry in guild.audit_logs(limit=1, action=action):
                if entry.user and entry.user.id != self.bot.user.id:
                    return guild.get_member(entry.user.id)
        except discord.Forbidden:
            pass
        return None

    # ── イベントリスナー ──────────────────────────────

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel: discord.abc.GuildChannel):
        guild = channel.guild
        cfg   = self._guild_cfg(guild.id)
        if not cfg.get("enabled") or not cfg.get("detect_channel_del", True):
            return
        count = self._record_delete(guild.id, "channel", cfg.get("ch_window", 10))
        if count >= cfg.get("ch_threshold", 3):
            executor = await self._get_executor(guild, discord.AuditLogAction.channel_delete)
            await self._punish_and_recover(guild, executor, "チャンネル大量削除", auto_restore=cfg.get("auto_restore", True))

    @commands.Cog.listener()
    async def on_guild_channel_create(self, channel: discord.abc.GuildChannel):
        guild = channel.guild
        if self._guild_cfg(guild.id).get("enabled"):
            await self._update_backup(guild)

    @commands.Cog.listener()
    async def on_guild_role_delete(self, role: discord.Role):
        guild = role.guild
        cfg   = self._guild_cfg(guild.id)
        if not cfg.get("enabled") or not cfg.get("detect_role_del", True):
            return
        count = self._record_delete(guild.id, "role", cfg.get("role_window", 10))
        if count >= cfg.get("role_threshold", 3):
            executor = await self._get_executor(guild, discord.AuditLogAction.role_delete)
            await self._punish_and_recover(guild, executor, "ロール大量削除", auto_restore=cfg.get("auto_restore", True))

    @commands.Cog.listener()
    async def on_guild_role_create(self, role: discord.Role):
        guild = role.guild
        if self._guild_cfg(guild.id).get("enabled"):
            await self._update_backup(guild)

    @commands.Cog.listener()
    async def on_member_ban(self, guild: discord.Guild, user: discord.User):
        cfg = self._guild_cfg(guild.id)
        if not cfg.get("enabled") or not cfg.get("detect_mass_ban", True):
            return
        count = self._record_delete(guild.id, "ban", cfg.get("ban_window", 10))
        if count >= cfg.get("ban_threshold", 3):
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

    # ── スラッシュコマンド ────────────────────────────

    @app_commands.command(name="antiraid_panel", description="荒らし検知の詳細設定パネルをこのチャンネルに設置します")
    @app_commands.checks.has_permissions(administrator=True)
    async def antiraid_panel(self, interaction: discord.Interaction):
        embed = discord.Embed(
            title="🛡️ AntiRaid 設定パネル",
            description=(
                "管理者のみ操作できます。各ボタンの説明：\n\n"
                "**Row 1 ─ 荒らし対策の有効/無効・現在の設定確認**\n"
                "✅ 有効にする　❌ 無効にする　🛡️ 現在の設定を確認\n\n"
                "**Row 2 ─ 処罰方法（3種から選択）**\n"
                "🔨 BAN　👢 KICK　⏱️ タイムアウト（時間もここで設定）\n\n"
                "**Row 3 ─ 各検知のON/OFF切替**\n"
                "📁 チャンネル削除検知　🎭 ロール削除検知　🔨 大量BAN検知\n\n"
                "**Row 4 ─ 各検知の閾値・ウィンドウ設定**\n"
                "⚙️ チャンネル削除閾値　⚙️ ロール削除閾値　⚙️ 大量BAN閾値\n\n"
                "**Row 5 ─ ログ・復旧・ホワイトリスト**\n"
                "📋 ログチャンネル設定　🔄 自動復旧切替　➕WL追加　➖WL削除"
            ),
            color=discord.Color.blurple(),
            timestamp=discord.utils.utcnow()
        )
        embed.set_footer(text="Developer @yuzu09591")
        view = AntiRaidPanelView(self)
        await interaction.response.send_message(embed=embed, view=view)

    @app_commands.command(name="antiraid_recovery", description="復旧パネルをこのチャンネルに設置します")
    @app_commands.checks.has_permissions(administrator=True)
    async def antiraid_recovery(self, interaction: discord.Interaction):
        embed = discord.Embed(
            title="🔄 AntiRaid 復旧パネル",
            description=(
                "管理者のみ操作できます。\n\n"
                "🔄 **全体復旧** → ロール・カテゴリ・チャンネルをすべて復旧\n"
                "📁 **チャンネル＆カテゴリのみ復旧**\n"
                "🎭 **ロールのみ復旧**\n"
                "📦 **バックアップ取得** → 今すぐ保存\n"
                "📋 **バックアップ情報確認**\n\n"
                "⚠️ 復旧前に必ずバックアップを取得してください。"
            ),
            color=discord.Color.orange(),
            timestamp=discord.utils.utcnow()
        )
        embed.set_footer(text="Developer @yuzu09591")
        view = RecoveryPanelView(self)
        await interaction.response.send_message(embed=embed, view=view)


async def setup(bot: commands.Bot):
    await bot.add_cog(AntiRaid(bot))
