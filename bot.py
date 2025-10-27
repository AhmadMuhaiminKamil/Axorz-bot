# bot.py
import os
import re
import aiosqlite
import discord
from discord import app_commands
from discord.ext import commands
from datetime import datetime
from urllib.parse import urlparse
import sys

# -------- CONFIG ----------
DATABASE = os.environ.get("LINKBOT_DB", "links.db")
BOT_TOKEN = os.environ.get("DISCORD_TOKEN")  # <-- must be set in env
GUILD_ID = os.environ.get("GUILD_ID")  # optional: restrict commands to a guild for faster registration
# --------------------------

if not BOT_TOKEN:
    print("Error: DISCORD_TOKEN environment variable is not set. Set DISCORD_TOKEN before running the bot.")
    sys.exit(1)

intents = discord.Intents.default()
intents.message_content = False  # not needed for slash commands in this bot
bot = commands.Bot(command_prefix="!", intents=intents)

# helper: URL validation (basic)
URL_REGEX = re.compile(
    r'^(https?:\/\/)?'              # http:// or https:// (optional)
    r'([A-Za-z0-9-]+\.)+[A-Za-z]{2,}'  # domain...
    r'(:\d+)?'                      # optional port
    r'(\/\S*)?$'                    # optional path
)

def is_valid_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
        if parsed.scheme and parsed.netloc:
            return True
        # allow urls without scheme like example.com
        return bool(URL_REGEX.match(url))
    except Exception:
        return False

# Database setup
async def init_db():
    async with aiosqlite.connect(DATABASE) as db:
        await db.execute("""
        CREATE TABLE IF NOT EXISTS links (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            url TEXT NOT NULL,
            title TEXT,
            tags TEXT,
            added_by_id INTEGER NOT NULL,
            added_by_name TEXT,
            added_at TEXT NOT NULL
        )
        """)
        await db.commit()

@bot.event
async def on_ready():
    # initialize DB
    await init_db()

    # set bot presence (status + activity)
    try:
        await bot.change_presence(
            activity=discord.Activity(
                type=discord.ActivityType.watching,  # .playing / .listening / .watching
                name="saved links 📚"
            ),
            status=discord.Status.online
        )
    except Exception as pres_ex:
        print("Warning: failed to set presence:", pres_ex)

    # register commands (guild or global)
    if GUILD_ID:
        try:
            guild = discord.Object(id=int(GUILD_ID))
            # copy global commands to guild for faster registration during development
            bot.tree.copy_global_to(guild=guild)
            await bot.tree.sync(guild=guild)
            print(f"Slash commands synced to guild {GUILD_ID}")
        except Exception as e:
            print("Failed to sync guild commands:", e)
    else:
        # global sync (can take up to an hour to appear)
        try:
            await bot.tree.sync()
            print("Global slash commands synced.")
        except Exception as e:
            print("Failed to sync global commands:", e)

    print(f"Logged in as {bot.user} (ID: {bot.user.id})")

# -------- Slash commands --------
LINKS = app_commands.Group(name="link", description="Commands for saving and retrieving links")

@LINKS.command(name="save", description="Simpan link ke database")
@app_commands.describe(url="URL untuk disimpan", title="Judul/label (opsional)", tags="Tags dipisah koma (opsional)")
async def link_save(interaction: discord.Interaction, url: str, title: str = None, tags: str = None):
    await interaction.response.defer(thinking=True)
    if not is_valid_url(url):
        await interaction.followup.send("URL tidak valid. Pastikan pakai format `https://example.com` atau `example.com`.", ephemeral=True)
        return

    tags_norm = None
    if tags:
        tags_list = [t.strip().lower() for t in tags.split(",") if t.strip()]
        tags_norm = ",".join(tags_list) if tags_list else None

    added_at = datetime.utcnow().isoformat()
    async with aiosqlite.connect(DATABASE) as db:
        cur = await db.execute(
            "INSERT INTO links (url, title, tags, added_by_id, added_by_name, added_at) VALUES (?, ?, ?, ?, ?, ?)",
            (url, title, tags_norm, interaction.user.id, str(interaction.user), added_at)
        )
        await db.commit()
        rowid = cur.lastrowid

    main_label = title if title else url

    embed = discord.Embed(title=main_label, color=0x2ecc71, timestamp=datetime.utcnow())
    embed.add_field(name="Link", value=f"<{url}>", inline=False)
    if tags_norm:
        embed.add_field(name="Tags", value=tags_norm, inline=False)
    embed.set_footer(text=f"ID: {rowid} • Disimpan oleh {interaction.user}", icon_url=interaction.user.display_avatar.url if interaction.user.display_avatar else None)

    await interaction.followup.send(embed=embed)

@LINKS.command(name="list", description="Tampilkan daftar link terbaru (maks 10). Bisa filter per tag.")
@app_commands.describe(tag="(opsional) filter berdasarkan tag")
async def link_list(interaction: discord.Interaction, tag: str = None):
    await interaction.response.defer()
    async with aiosqlite.connect(DATABASE) as db:
        if tag:
            tag_norm = tag.strip().lower()
            q = "SELECT id, url, title, tags, added_by_name, added_at FROM links WHERE tags LIKE ? ORDER BY id DESC LIMIT 50"
            params = (f"%{tag_norm}%",)
            cur = await db.execute(q, params)
        else:
            q = "SELECT id, url, title, tags, added_by_name, added_at FROM links ORDER BY id DESC LIMIT 50"
            cur = await db.execute(q)
        rows = await cur.fetchall()

    if not rows:
        await interaction.followup.send("Tidak ada link yang ditemukan.", ephemeral=True)
        return

    total = len(rows)
    to_show = rows[:10]

    embed = discord.Embed(title="Daftar Link", color=0x3498db, timestamp=datetime.utcnow())
    for r in to_show:
        lid, url, title, tags, added_by, added_at = r
        label = title if title else url
        value_lines = [f"<{url}>"]
        meta = []
        if tags:
            meta.append(f"Tags: {tags}")
        meta.append(f"By: {added_by}")
        meta.append(f"ID: {lid}")
        value_lines.append(" • ".join(meta))
        value = "\n".join(value_lines)
        if len(value) > 1024:
            value = value[:1021] + "..."
        embed.add_field(name=label, value=value, inline=False)

    footer_text = f"Menampilkan {len(to_show)} dari {total} hasil. Gunakan /link get <id> untuk info lengkap."
    embed.set_footer(text=footer_text)
    await interaction.followup.send(embed=embed)

@LINKS.command(name="get", description="Tampilkan detail link berdasarkan ID")
@app_commands.describe(id="ID link (angka)")
async def link_get(interaction: discord.Interaction, id: int):
    await interaction.response.defer()
    async with aiosqlite.connect(DATABASE) as db:
        cur = await db.execute("SELECT id, url, title, tags, added_by_name, added_at, added_by_id FROM links WHERE id = ?", (id,))
        row = await cur.fetchone()
    if not row:
        await interaction.followup.send(f"Tidak ditemukan link dengan ID `{id}`.", ephemeral=True)
        return

    lid, url, title, tags, added_by, added_at, added_by_id = row
    main_label = title if title else url
    embed = discord.Embed(title=main_label, color=0x9b59b6, timestamp=datetime.utcnow())
    embed.add_field(name="Link", value=f"<{url}>", inline=False)
    embed.add_field(name="Tags", value=tags if tags else "-", inline=False)
    embed.add_field(name="Ditambahkan oleh", value=added_by, inline=True)
    embed.add_field(name="Waktu (UTC)", value=added_at, inline=True)
    embed.set_footer(text=f"ID: {lid} • Gunakan /link remove <id> untuk menghapus jika kamu yang menambahkan atau punya permission Manage Messages.")

    await interaction.followup.send(embed=embed)

@LINKS.command(name="remove", description="Hapus link berdasarkan ID (hanya pemilik atau yang punya permission)")
@app_commands.describe(id="ID link (angka)")
async def link_remove(interaction: discord.Interaction, id: int):
    await interaction.response.defer()
    async with aiosqlite.connect(DATABASE) as db:
        cur = await db.execute("SELECT added_by_id, url, title FROM links WHERE id = ?", (id,))
        row = await cur.fetchone()
        if not row:
            await interaction.followup.send(f"Tidak ditemukan link dengan ID `{id}`.", ephemeral=True)
            return
        added_by_id, url, title = row

        # permission check
        if interaction.user.id != added_by_id and not interaction.user.guild_permissions.manage_messages:
            await interaction.followup.send("Kamu tidak punya izin untuk menghapus link ini. Hanya yang menambahkan atau yang punya `Manage Messages` yang bisa menghapus.", ephemeral=True)
            return

        await db.execute("DELETE FROM links WHERE id = ?", (id,))
        await db.commit()

    await interaction.followup.send(f"Link ID `{id}` berhasil dihapus. ({title or url})", ephemeral=True)

# register the group with the bot
bot.tree.add_command(LINKS)

# Run the bot
if __name__ == "__main__":
    bot.run(BOT_TOKEN)
