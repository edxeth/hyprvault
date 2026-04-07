import sys
import asyncio
import argparse
import shutil
import subprocess
import time
from .save import save_session
from .load import restore_session
from .utils import GREEN, BLUE, YELLOW, RED, BOLD, RESET, list_sessions, get_config_dir
from .delete import delete_session


def print_banner():
    banner = f"""
{RED}{BOLD}╔═══════════════════════════════════════════════════════════════════════════════╗
{BLUE}║                                                                               ║
║   {BOLD}██╗  ██╗██╗   ██╗██████╗ ██████╗ ██╗   ██╗ █████╗ ██╗   ██╗██╗  ████████╗{RESET}{BLUE}   ║
║   {BOLD}██║  ██║╚██╗ ██╔╝██╔══██╗██╔══██╗██║   ██║██╔══██╗██║   ██║██║  ╚══██╔══╝{RESET}{BLUE}   ║
║   {BOLD}███████║ ╚████╔╝ ██████╔╝██████╔╝██║   ██║███████║██║   ██║██║     ██║   {RESET}{BLUE}   ║
║   {BOLD}██╔══██║  ╚██╔╝  ██╔═══╝ ██╔══██╗╚██╗ ██╔╝██╔══██║██║   ██║██║     ██║   {RESET}{BLUE}   ║
║   {BOLD}██║  ██║   ██║   ██║     ██║  ██║ ╚████╔╝ ██║  ██║╚██████╔╝███████╗██║   {RESET}{BLUE}   ║
║   {BOLD}╚═╝  ╚═╝   ╚═╝   ╚═╝     ╚═╝  ╚═╝  ╚═══╝  ╚═╝  ╚═╝ ╚═════╝ ╚══════╝╚═╝   {RESET}{BLUE}   ║
║                                                                               ║
{RED}╚═══════════════════════════════════════════════════════════════════════════════╝{RESET}
{GREEN}{BOLD}                         Hyprland Configuration Manager {RESET}
"""
    print(banner)


def print_help():
    print_banner()
    print(f"{BOLD}USAGE:{RESET}")
    print(f"  hyprvault {GREEN}<action>{RESET} {BLUE}[session_name]{RESET}\n")

    print(f"{BOLD}ACTIONS:{RESET}")
    print(
        f"  {GREEN}save{RESET}    Capture the current state of all windows and save to a JSON file."
    )
    print(
        f"  {GREEN}load{RESET}    Restore windows to their saved workspaces, positions, and states."
    )
    print(f"  {GREEN}list{RESET}    List all saved sessions.")
    print(f"  {GREEN}delete{RESET}  Delete a saved session.")
    print(f"  {GREEN}gui{RESET}     Choose a session in Walker and restore it.")
    print(f"  {GREEN}gui-save{RESET} Save a session from Walker.")
    print(f"  {GREEN}help{RESET}    Show this magnificent help message.")

    print(f"\n{BOLD}EXAMPLES:{RESET}")
    print(
        f"  hyprvault {GREEN}save{RESET} {BLUE}my_workspace{RESET}  -> Saves to ~/.config/hyprvault/sessions/my_workspace.json"
    )
    print(
        f"  hyprvault {GREEN}load{RESET} {BLUE}my_workspace{RESET}  -> Loads 'my_workspace'"
    )
    print(f"  hyprvault {GREEN}list{RESET}             -> Shows all saved sessions")
    print(
        f"  hyprvault {GREEN}delete{RESET} {BLUE}my_workspace{RESET}    -> Deletes 'my_workspace'"
    )
    print(
        f"  hyprvault {GREEN}gui{RESET}                -> Opens Walker to choose a session"
    )
    print(
        f"  hyprvault {GREEN}gui-save{RESET}           -> Opens Walker to enter a session name"
    )

    print(f"\n{BOLD}SESSION DIR:{RESET} {get_config_dir()}")
    print(
        f"\n{YELLOW}Note: Ensure Hyprland is running before executing load commands.{RESET}"
    )


def choose_with_walker(options, prompt):
    if not shutil.which("walker"):
        print(f"{RED}Walker is not installed.{RESET}")
        return None

    time.sleep(0.15)
    proc = subprocess.run(
        [
            "walker",
            "-d",
            "-e",
            "-t",
            "pi",
            "-p",
            prompt,
        ],
        input=("\n".join(options) + "\n") if options else "",
        text=True,
        capture_output=True,
    )

    choice = proc.stdout.strip()
    return choice or None



def choose_session_with_walker():
    sessions = list_sessions()
    if not sessions:
        subprocess.run(
            ["notify-send", "HyprVault", "No saved sessions"], check=False
        )
        return None

    return choose_with_walker(sessions, "Load HyprVault session")



def choose_restore_mode_with_walker():
    return choose_with_walker(
        ["Replace current windows", "Keep current windows", "Delete session"],
        "How should this session load?",
    )



def choose_yes_no_with_walker(prompt):
    return choose_with_walker(["Yes", "No"], prompt)



def choose_session_name_with_walker():
    return choose_with_walker([], "Save HyprVault session")


async def main():
    parser = argparse.ArgumentParser(
        description="Hyprland Session Manager", add_help=False
    )

    parser.add_argument("-h", "--help", action="store_true")

    parser.add_argument(
        "action",
        nargs="?",
        choices=["save", "load", "list", "delete", "gui", "gui-save", "help"],
        help="Action to be performed",
    )
    parser.add_argument("name", nargs="?", default="last_session", help="Session name")

    args = parser.parse_args()

    if args.help or args.action is None or args.action == "help":
        print_help()
        return

    if args.action == "save":
        print(f"{YELLOW}[*]{RESET} Saving session: {BOLD}{args.name}{RESET}...")
        try:
            save_session(args.name)
        except Exception as e:
            print(f"{RED}[-]{RESET} An error occurred: {e}")

    elif args.action == "load":
        print(f"{BLUE}[*]{RESET} Loading session: {BOLD}{args.name}{RESET}...")
        try:
            await restore_session(args.name)
        except Exception as e:
            print(f"{RED}[-]{RESET} Session load error: {e}")

    elif args.action == "gui":
        chosen = choose_session_with_walker()
        if chosen:
            mode = choose_restore_mode_with_walker()
            if not mode:
                return

            if mode == "Delete session":
                confirm = choose_yes_no_with_walker(f"Delete session '{chosen}'?")
                if confirm == "Yes":
                    delete_session(chosen)
                return

            clean = mode == "Replace current windows"
            print(f"{BLUE}[*]{RESET} Loading session: {BOLD}{chosen}{RESET}...")
            try:
                await restore_session(chosen, clean=clean)
            except Exception as e:
                print(f"{RED}[-]{RESET} Session load error: {e}")

    elif args.action == "gui-save":
        name = choose_session_name_with_walker()
        if name:
            overwrite = None
            if name in list_sessions():
                confirm = choose_yes_no_with_walker(f"Overwrite session '{name}'?")
                if confirm != "Yes":
                    save_session(name, overwrite=False)
                    return
                overwrite = True

            print(f"{YELLOW}[*]{RESET} Saving session: {BOLD}{name}{RESET}...")
            try:
                save_session(name, overwrite=overwrite)
            except Exception as e:
                print(f"{RED}[-]{RESET} An error occurred: {e}")

    elif args.action == "list":
        sessions = list_sessions()
        if sessions:
            print(f"{GREEN}{BOLD}Saved Sessions:{RESET}")
            for s in sessions:
                print(f"  • {s}")
        else:
            print(f"{YELLOW}No saved sessions found.{RESET}")
            print(f"Use {GREEN}hyprvault save <name>{RESET} to create one.")

    elif args.action == "delete":
        print(f"{RED}[*]{RESET} Deleting session: {BOLD}{args.name}{RESET}...")
        try:
            delete_session(args.name)
        except Exception as e:
            print(f"{RED}[-]{RESET} Session deletion error: {e}")


def main_entry():
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)


if __name__ == "__main__":
    main_entry()
