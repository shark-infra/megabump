from config import BOT_TOKEN, ACCESS_ROLE

import discord
from discord.ext.pages import Paginator, Page, PaginatorButton
from datetime import datetime
import time
from current_state import CurrentState
from integerate import start_integerate
import megabump_utils as mb
import re
import subprocess
from io import BytesIO

bot = discord.Bot()


def get_truncated_embed(title, desc, color=0x3C09C8):
    if len(desc) > 1500:
        title += " (truncated)"
    trim = desc[-1500:]
    return discord.Embed(
        title=title,
        description=trim,
        color=color,
    )


def get_status():
    subprocess.run([mb.repo_path / "scripts/llvm_revision", "fetch"])
    state = CurrentState({})
    return state.new_commits


def advance_to(commit_id):
    out = subprocess.run(
        [mb.repo_path / "scripts/llvm_revision", "next", f"--advance-to={commit_id}"],
    )
    if out.returncode != 0:
        # Handle this case better
        return False
    return True


def push_current_iree_to_github(branch_name, force=False):
    mb.git_push_branch("origin", branch_name, repo_dir=mb.iree_path, force=force)


def get_branch_name(thread_id) -> str | None:
    # Get the title of this thread
    try:
        thread = bot.get_channel(thread_id)
        assert isinstance(thread, discord.Thread)
        return thread.name
    except Exception as e:
        print(e)
        return None


def parse_desc(desc):
    # Regex: <commit_desc> optional(<#pr_num>) (<author> on <date>)
    # Groups: commit_desc, pr_num, author, date
    regex = re.compile(
        r"(?P<commit_desc>.*?)\s*(?:\((?P<pr_num>#\d+)\))?\s*\((?P<author>.*?) on (?P<date>.*?)\)"
    )
    match = regex.match(desc)
    if match:
        return match.groups()
    return None


def get_commit_embed(commit_id, desc):
    parsed = parse_desc(desc)
    if parsed is None:
        embed = discord.Embed(
            title=commit_id,
            url=f"https://github.com/llvm/llvm-project/commit/{commit_id}",
            description=desc,
            color=0x3C09C8,
        )
        return embed
    else:
        commit_desc, pr_num, author, date = parsed
        embed = discord.Embed(
            title=commit_id,
            url=f"https://github.com/llvm/llvm-project/commit/{commit_id}",
            description=commit_desc,
            color=0x3C09C8,
        )
        embed.set_author(name=author)
        embed.add_field(name="Date", value=date, inline=True)
        if pr_num is None:
            gh_link = "No PR"
        else:
            gh_link = f"https://github.com/llvm/llvm-project/pull/{pr_num[1:]}"
        embed.add_field(name="Github PR", value=gh_link, inline=True)
        return embed


async def check_role(ctx):
    # Check if author has ACCESS_ROLE
    if not any(role.name == ACCESS_ROLE for role in ctx.author.roles):
        await ctx.respond(
            f"You need the role: {ACCESS_ROLE} to use this command", ephemeral=True
        )
        return False
    return True


async def check_channel(ctx: discord.ApplicationContext):
    branch_name = get_branch_name(ctx.channel_id)
    if branch_name is None:
        await ctx.respond(
            f"Branch not found. Please start an integerate first or use this command from the integerate thread..",
            ephemeral=True,
        )
        return False
    current_branch = mb.git_current_branch(repo_dir=mb.iree_path)
    if current_branch != branch_name:
        await ctx.respond(
            f"Trying to get status for branch {branch_name} but current integerate branch is {current_branch}. Please start a new integerate or use the command from the correct thread.",
            ephemeral=True,
        )
        return False
    return True


class AdvanceToButton(PaginatorButton):
    def __init__(self):
        emoji = discord.PartialEmoji(name="github_actions", id=1009215250991697949)
        super().__init__(
            "advance_to",
            label="Advance To This Commit",
            emoji=emoji,
            style=discord.ButtonStyle.green,
            row=1,
        )

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer()

        assert isinstance(self.paginator, Paginator)
        curr_page = self.paginator.pages[self.paginator.current_page]
        commit_id = curr_page.embeds[0].title

        # Cancel the paginator and replace it with a single embed
        await self.paginator.cancel(
            page=get_truncated_embed(f"Advancing to commit {commit_id}", f"")
        )
        message = interaction.message
        assert message is not None

        try:
            advance_to(commit_id)
        except Exception as e:
            print(e)
            return await message.edit(
                embed=get_truncated_embed(f"Error advancing to commit {commit_id}", e)
            )

        return await message.edit(
            embed=get_truncated_embed(
                f"Advanced to commit {commit_id} Successfully", f""
            )
        )


class BuildButton(PaginatorButton):
    def __init__(self):
        emoji = discord.PartialEmoji(name="cmake", id=723710645631057940)
        super().__init__(
            "build",
            label="Build And Test",
            style=discord.ButtonStyle.blurple,
            emoji=emoji,
            row=1,
        )

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer()

        def get_truncated_embed(title, desc):
            if len(desc) > 1500:
                title += " (truncated)"
            trim = desc[-1500:]
            return discord.Embed(
                title=title,
                description=trim,
                color=0x3C09C8,
            )

        # Send the embed
        logs = "Building and testing...\n"

        # Cancel the paginator and replace it with a single embed
        assert isinstance(self.paginator, Paginator)
        await self.paginator.cancel(
            page=get_truncated_embed(
                "Building and Testing... (streamed every 10 seconds)", logs
            )
        )
        message = interaction.message
        assert message is not None

        # Run the process and keep outputing to the embed
        process = subprocess.Popen(
            [mb.repo_path / "scripts/build_and_validate.sh"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            universal_newlines=True,
            bufsize=1024,
        )

        # Send logs every 10 seconds
        curr_time = time.time()
        for line in iter(process.stdout.readline, ""):
            logs += line
            if time.time() - curr_time > 10:
                curr_time = time.time()
                try:
                    await message.edit(
                        embed=get_truncated_embed(
                            "Building and Testing... (streamed every 10 seconds)", logs
                        )
                    )
                except Exception as e:
                    print(e)

        return_code = process.wait()

        if return_code != 0:
            await message.edit(embed=get_truncated_embed("Build and Test Failed", logs))

            error_lines = []
            for line in logs.split("\n"):
                if (
                    line.startswith("FAILED:")
                    or "error:" in line
                    or "Assertion" in line
                ):
                    error_lines.append(line)

            errors = "\n".join(error_lines)
            channel = message.channel
            # Check length of error message
            if len(errors) < 1500:
                # Send as embed
                await channel.send(
                    embed=get_truncated_embed("Summarized Errors", errors)
                )
            else:
                await channel.send(
                    embed=get_truncated_embed("Summarized Errors", logs),
                    file=discord.File(
                        BytesIO(errors.encode("utf-8")), filename="errors.txt"
                    ),
                )
        else:
            await message.edit(
                embed=get_truncated_embed("Build and Test Successful", logs)
            )
            channel = message.channel
            await channel.send(f"Build and Test Successful")


class PushButton(PaginatorButton):
    def __init__(self):
        emoji = discord.PartialEmoji(name="ireeghost", id=841412367001714688)
        super().__init__(
            "push",
            label="Push to IREE Github",
            style=discord.ButtonStyle.secondary,
            emoji=emoji,
            row=1,
        )

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer()

        # Cancel the paginator and replace it with a single embed
        assert isinstance(self.paginator, Paginator)
        await self.paginator.cancel(
            page=get_truncated_embed("Pushing to Github...", f"")
        )
        message = interaction.message
        assert message is not None

        try:
            push_current_iree_to_github(get_branch_name(message.channel.id))
        except Exception as e:
            print(e)
            return await message.edit(
                embed=get_truncated_embed("Error Pushing to Github", e)
            )

        return await message.edit(
            embed=get_truncated_embed("Push to Github Successfully", "")
        )


@bot.slash_command()
async def status(ctx: discord.ApplicationContext):
    if not await check_role(ctx):
        return

    if not await check_channel(ctx):
        return

    # Defer the slash command since getting the commits may take a while
    await ctx.defer()

    commits = get_status()
    pages = [
        Page(embeds=[get_commit_embed(commit[0], commit[1])]) for commit in commits
    ]

    paginator = Paginator(pages=pages, author_check=True, disable_on_timeout=True)
    paginator.add_button(AdvanceToButton())
    paginator.add_button(BuildButton())
    paginator.add_button(PushButton())

    return await paginator.respond(ctx.interaction)


@bot.slash_command()
async def integerate(ctx: discord.ApplicationContext):
    if not await check_role(ctx):
        return

    # Defer the slash command since starting the integerate may take a while
    await ctx.defer()

    # Start the integerate and push the branch to IREE upstream
    try:
        branch_name = start_integerate(None)
        push_current_iree_to_github(branch_name)
    except Exception as e:
        print(e)
        return await ctx.send_followup(
            f"There was an error while starting the integerate"
        )

    message = await ctx.send(
        f"Date :{datetime.now().date()}\nAuthor: {ctx.author.mention}"
    )
    thread = await message.create_thread(name=branch_name, auto_archive_duration=10080)
    message = await thread.send(
        f"Create a PR for this integerate: https://github.com/openxla/iree/pull/new/{branch_name}"
    )
    return await ctx.send_followup("Integerate Started Successfully!")


bot.run(BOT_TOKEN)
