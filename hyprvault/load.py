import asyncio
import collections
import json
import os
import shlex
import time
from pathlib import Path

from .utils import (
    DOCKER_DESKTOP_OPEN_COMMAND,
    GREEN,
    RED,
    RESET,
    TERMINAL_EMULATORS,
    YELLOW,
    format_cmdline,
    get_session_path,
    leaf_cmdline,
    normalize_command_string,
    read_cmdline,
)

HYPR_V = 0.0
# Enable restore tracing with HYPRVAULT_TRACE_ACTIONS=1.
# Optional: override the log file path with HYPRVAULT_TRACE_PATH.
TRACE_ENV_VAR = "HYPRVAULT_TRACE_ACTIONS"
TRACE_ENABLED = os.environ.get(TRACE_ENV_VAR, "").lower() in {"1", "true", "yes", "on"}
TRACE_PATH = Path(os.environ.get("HYPRVAULT_TRACE_PATH", "/tmp/hyprvault-action-trace.log"))


def trace(message):
    if not TRACE_ENABLED:
        return

    TRACE_PATH.parent.mkdir(parents=True, exist_ok=True)
    timestamp = time.strftime("%H:%M:%S")
    with TRACE_PATH.open("a", encoding="utf-8") as f:
        f.write(f"[{timestamp}] {message}\n")


async def dispatch(cmd_args, wait=True):
    trace(f"dispatch wait={wait} args={cmd_args}")
    proc = await asyncio.create_subprocess_exec(
        "hyprctl",
        "dispatch",
        *cmd_args,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    if wait:
        return await proc.wait() == 0
    return None


async def init_hypr_config():
    global HYPR_V
    proc = await asyncio.create_subprocess_exec(
        "hyprctl", "version", "-j", stdout=asyncio.subprocess.PIPE
    )
    stdout, _ = await proc.communicate()
    try:
        v_data = json.loads(stdout)
        v_parts = v_data.get("version", "0.0.0").split(".")
        HYPR_V = float(f"{v_parts[0]}.{v_parts[1]}")
    except Exception:
        HYPR_V = 0.0


async def get_clients():
    proc = await asyncio.create_subprocess_exec(
        "hyprctl", "clients", "-j", stdout=asyncio.subprocess.PIPE
    )
    stdout, _ = await proc.communicate()
    clients = json.loads(stdout)
    return [client for client in clients if client.get("mapped", True)]


async def get_active_window_address():
    proc = await asyncio.create_subprocess_exec(
        "hyprctl", "activewindow", "-j", stdout=asyncio.subprocess.PIPE
    )
    stdout, _ = await proc.communicate()
    try:
        return json.loads(stdout).get("address")
    except Exception:
        return None


async def focus_window(addr, timeout=1.0):
    trace(f"focus_window start addr={addr} timeout={timeout}")
    await dispatch(["focuswindow", f"address:{addr}"])
    attempts = max(1, int(timeout / 0.05))
    for _ in range(attempts):
        if await get_active_window_address() == addr:
            trace(f"focus_window success addr={addr}")
            return True
        await asyncio.sleep(0.05)
    trace(f"focus_window failed addr={addr}")
    return False


def client_commands(client):
    pid = client.get("pid", 0)
    launcher = format_cmdline(read_cmdline(pid))
    leaf = format_cmdline(leaf_cmdline(pid))
    return launcher, leaf


def class_matches_saved_window(client_class, saved_class):
    client_normalized = (client_class or "").lower()
    saved_normalized = (saved_class or "").lower()

    if client_normalized == saved_normalized:
        return True

    client_suffix = client_normalized.rsplit(".", 1)[-1]
    saved_suffix = saved_normalized.rsplit(".", 1)[-1]
    if client_suffix == saved_suffix:
        return True

    return client_normalized in saved_normalized or saved_normalized in client_normalized



def client_matches_saved_window(client, sw):
    if not class_matches_saved_window(client.get("class"), sw["class_name"]):
        return False

    saved_launcher = normalize_command_string(sw.get("match_command") or sw.get("command", ""))
    saved_leaf = normalize_command_string(sw.get("leaf_command", ""))
    launcher, leaf = client_commands(client)

    if saved_launcher and launcher != saved_launcher:
        return False
    if saved_leaf and leaf != saved_leaf:
        return False
    return True


async def wait_for_spawned_window(sw, existing_addresses: set[str], timeout=6.0):
    attempts = max(1, int(timeout / 0.1))
    for _ in range(attempts):
        clients = await get_clients()
        for client in clients:
            addr = client.get("address")
            if not addr or addr in existing_addresses:
                continue
            if client_matches_saved_window(client, sw):
                return client
        await asyncio.sleep(0.1)
    return None


async def wait_for_spawned_class_window(class_name, existing_addresses: set[str], timeout=6.0):
    attempts = max(1, int(timeout / 0.1))
    for _ in range(attempts):
        clients = await get_clients()
        for client in clients:
            addr = client.get("address")
            if not addr or addr in existing_addresses:
                continue
            if class_matches_saved_window(client.get("class"), class_name):
                return client
        await asyncio.sleep(0.1)
    return None


def is_ambiguous_terminal_window(sw):
    if not sw["class_name"]:
        return False
    normalized = sw["class_name"].lower().rsplit(".", 1)[-1]
    if normalized not in TERMINAL_EMULATORS:
        return False

    if sw.get("leaf_command"):
        return False

    launcher = normalize_command_string(sw.get("match_command") or sw.get("command", ""))
    if not launcher:
        return False

    try:
        argv = shlex.split(launcher)
    except Exception:
        argv = [launcher]

    return len(argv) == 1 and Path(argv[0]).name.lower() in TERMINAL_EMULATORS


def window_signature(sw):
    return (
        (sw.get("class_name") or "").lower(),
        normalize_command_string(sw.get("match_command") or sw.get("command", "")),
        normalize_command_string(sw.get("leaf_command", "")),
    )



def find_best_match(sw, current_clients, used_addresses: set):
    saved_launcher = normalize_command_string(sw.get("match_command") or sw.get("command", ""))
    saved_leaf = normalize_command_string(sw.get("leaf_command", ""))

    if is_ambiguous_terminal_window(sw):
        return None

    for c in current_clients:
        addr = c.get("address")
        if addr in used_addresses:
            continue
        if client_matches_saved_window(c, sw):
            return c

    if saved_launcher or saved_leaf:
        return None

    for c in current_clients:
        addr = c.get("address")
        if addr in used_addresses:
            continue
        if c.get("class") == sw["class_name"]:
            return c

    return None


def split_tiled_windows(saved_windows):
    if len(saved_windows) <= 1:
        return None, None, None

    bbox_left = min(sw["at"][0] for sw in saved_windows)
    bbox_top = min(sw["at"][1] for sw in saved_windows)
    bbox_right = max(sw["at"][0] + sw["size"][0] for sw in saved_windows)
    bbox_bottom = max(sw["at"][1] + sw["size"][1] for sw in saved_windows)
    bbox_width = bbox_right - bbox_left
    bbox_height = bbox_bottom - bbox_top

    by_x = sorted(saved_windows, key=lambda sw: (sw["at"][0], sw["at"][1]))
    vertical_split = None
    for i in range(1, len(by_x)):
        left = by_x[:i]
        right = by_x[i:]
        left_max = max(sw["at"][0] + sw["size"][0] for sw in left)
        right_min = min(sw["at"][0] for sw in right)
        if left_max <= right_min:
            vertical_split = (left, right)
            break

    by_y = sorted(saved_windows, key=lambda sw: (sw["at"][1], sw["at"][0]))
    horizontal_split = None
    for i in range(1, len(by_y)):
        top = by_y[:i]
        bottom = by_y[i:]
        top_max = max(sw["at"][1] + sw["size"][1] for sw in top)
        bottom_min = min(sw["at"][1] for sw in bottom)
        if top_max <= bottom_min:
            horizontal_split = (top, bottom)
            break

    if vertical_split and horizontal_split:
        if bbox_width >= bbox_height:
            return "vertical", vertical_split[0], vertical_split[1]
        return "horizontal", horizontal_split[0], horizontal_split[1]
    if vertical_split:
        return "vertical", vertical_split[0], vertical_split[1]
    if horizontal_split:
        return "horizontal", horizontal_split[0], horizontal_split[1]
    return None, None, None


def order_tiled_windows(saved_windows):
    if len(saved_windows) <= 1:
        return saved_windows

    orientation, first, second = split_tiled_windows(saved_windows)
    if not orientation:
        return sorted(saved_windows, key=lambda sw: (sw["at"][1], sw["at"][0]))

    if orientation == "vertical":
        return order_tiled_windows(first) + order_tiled_windows(second)

    return order_tiled_windows(first) + order_tiled_windows(second)


def is_late_prone_window(sw):
    if sw.get("class_name") == "Docker Desktop":
        return True

    cmd = normalize_command_string(sw.get("match_command") or sw.get("command", ""))
    return "electron" in cmd



def spawned_window_timeout(sw):
    if sw.get("class_name") == "Docker Desktop":
        return 12.0

    normalized_class = (sw.get("class_name") or "").lower().rsplit(".", 1)[-1]
    if normalized_class in TERMINAL_EMULATORS:
        return 12.0

    return 30.0 if is_late_prone_window(sw) else 6.0



def needs_tiled_stabilization(sw):
    return is_late_prone_window(sw) and sw.get("class_name") != "Docker Desktop"



def client_matches_saved_placement(client, sw):
    return (
        client.get("workspace", {}).get("id") == sw["workspace_id"]
        and client.get("at") == sw.get("at")
        and client.get("size") == sw.get("size")
    )



def stabilization_observe_window(sw):
    if is_late_prone_window(sw) and sw.get("class_name") != "Docker Desktop":
        return 4.5
    return 0.0



def opposite_direction(direction):
    return {"r": "l", "l": "r", "d": "u", "u": "d"}.get(direction, direction)



def build_tiled_restore_plan(saved_windows):
    if not saved_windows:
        return None, []
    if len(saved_windows) == 1:
        return saved_windows[0], []

    orientation, first, second = split_tiled_windows(saved_windows)
    if not orientation:
        ordered = order_tiled_windows(saved_windows)
        anchor = ordered[0]
        steps = [{"focus": anchor, "spawn": sw, "preselect": None} for sw in ordered[1:]]
        return anchor, steps

    anchor_first, steps_first = build_tiled_restore_plan(first)
    anchor_second, steps_second = build_tiled_restore_plan(second)
    direction = "r" if orientation == "vertical" else "d"

    if (
        len(saved_windows) == 2
        and len(first) == 1
        and len(second) == 1
        and is_late_prone_window(anchor_first)
        and not is_late_prone_window(anchor_second)
    ):
        return anchor_second, [
            {"focus": anchor_second, "spawn": anchor_first, "preselect": opposite_direction(direction)}
        ]

    return anchor_first, [
        {"focus": anchor_first, "spawn": anchor_second, "preselect": direction},
        *steps_first,
        *steps_second,
    ]


async def apply_window_state(sw, addr, current_fullscreen=0, move_workspace=True):
    ws_id = sw["workspace_id"]

    await focus_window(addr)
    await asyncio.sleep(0.2)

    if current_fullscreen > 0:
        await dispatch(["fullscreen", "0"])
        await asyncio.sleep(0.1)

    if sw["is_floating"]:
        await dispatch(["setfloating", f"address:{addr}"])
        await asyncio.sleep(0.05)
        await dispatch(
            [
                "resizewindowpixel",
                f"exact {sw['size'][0]} {sw['size'][1]},address:{addr}",
            ]
        )
        await dispatch(
            ["movewindowpixel", f"exact {sw['at'][0]} {sw['at'][1]},address:{addr}"]
        )
    elif move_workspace:
        await dispatch(["settiled", f"address:{addr}"])

    if move_workspace:
        await dispatch(["movetoworkspacesilent", f"{ws_id},address:{addr}"])
        await asyncio.sleep(0.1)

    saved_fs_state = sw.get("fullscreen", 0)
    if saved_fs_state > 0:
        await dispatch(["focuswindow", f"address:{addr}"])
        await asyncio.sleep(0.05)
        await dispatch(["fullscreen", str(saved_fs_state)])


async def restore_window(sw, matchable_clients, used_addresses: set, force_spawn=False):
    ws_id = sw["workspace_id"]
    match = None if force_spawn else find_best_match(sw, matchable_clients, used_addresses)

    if match:
        await asyncio.sleep(0.15)
        refreshed_clients = await get_clients()
        refreshed_match = next(
            (client for client in refreshed_clients if client.get("address") == match.get("address")),
            None,
        )
        if refreshed_match is None:
            match = None
        else:
            saved_launcher = normalize_command_string(sw.get("match_command") or sw.get("command", ""))
            saved_leaf = normalize_command_string(sw.get("leaf_command", ""))
            launcher, leaf = client_commands(refreshed_match)
            if saved_launcher and launcher != saved_launcher:
                match = None
            elif saved_leaf and leaf != saved_leaf:
                match = None
            else:
                match = refreshed_match

    if match:
        addr = match["address"]
        trace(f"restore_window reuse class={sw.get('class_name')} ws={ws_id} addr={addr}")
        used_addresses.add(addr)
        await apply_window_state(sw, addr, match.get("fullscreen", 0))
        return addr, sw.get("focus_history_id", 999)

    rules = f"workspace {ws_id} silent"
    if sw["is_floating"]:
        rules += f";float;move {sw['at'][0]} {sw['at'][1]};size {sw['size'][0]} {sw['size'][1]}"
    else:
        rules += ";tile"

    cmd = normalize_command_string(sw.get("command", ""))
    if sw.get("class_name") == "Docker Desktop":
        cmd = normalize_command_string(DOCKER_DESKTOP_OPEN_COMMAND)
    if cmd:
        closed_addresses = set()
        if force_spawn:
            # A preselect step replays to get deterministic tile placement, but
            # a live window already at the exact saved placement needs no replay:
            # closing it would only churn a correctly-placed application.
            exact_matches = [
                client
                for client in await find_live_matches(sw, workspace_id=ws_id)
                if client.get("address") not in used_addresses
                and client_matches_saved_placement(client, sw)
            ]
            if len(exact_matches) == 1:
                addr = exact_matches[0]["address"]
                trace(f"restore_window force-adopt class={sw.get('class_name')} ws={ws_id} addr={addr}")
                used_addresses.add(addr)
                return addr, sw.get("focus_history_id", 999)
            closed_addresses = await close_live_matches(sw, workspace_id=ws_id)
        live_clients = await get_clients()
        existing_addresses = {
            client.get("address")
            for client in live_clients
            if client.get("address") and client.get("address") not in closed_addresses
        }
        trace(f"restore_window spawn class={sw.get('class_name')} ws={ws_id} force_spawn={force_spawn} cmd={cmd}")
        print(f"{YELLOW}[*]{RESET} Spawning new window: {cmd}")
        proc = await asyncio.create_subprocess_exec(
            "hyprctl", "dispatch", "exec", f"[{rules}]", cmd
        )
        await proc.wait()
        if sw.get("class_name") == "Docker Desktop":
            spawned_match = await wait_for_spawned_class_window(
                sw.get("class_name"),
                existing_addresses,
                timeout=6.0,
            )
        else:
            spawned_match = await wait_for_spawned_window(
                sw,
                existing_addresses,
                timeout=spawned_window_timeout(sw),
            )
        if spawned_match:
            trace(f"restore_window spawned class={sw.get('class_name')} ws={ws_id} addr={spawned_match.get('address')} spawned_ws={spawned_match.get('workspace', {}).get('id')}")
            spawned_match = await stabilize_spawned_window(sw, spawned_match, used_addresses)
            addr = spawned_match["address"]
            used_addresses.add(addr)
            spawned_ws = spawned_match.get("workspace", {}).get("id")
            needs_workspace_fix = spawned_ws != ws_id
            if sw["is_floating"] or sw.get("fullscreen", 0) > 0 or needs_workspace_fix:
                await apply_window_state(
                    sw,
                    addr,
                    spawned_match.get("fullscreen", 0),
                    move_workspace=sw["is_floating"] or needs_workspace_fix,
                )
            await asyncio.sleep(2.0)
            return addr, sw.get("focus_history_id", 999)
    trace(f"restore_window no-match class={sw.get('class_name')} ws={ws_id}")
    return None, None


async def close_windows_on_workspaces(workspace_ids, timeout=10.0):
    workspace_ids = set(workspace_ids)
    attempts = max(1, int(timeout / 0.2))

    for _ in range(attempts):
        clients = await get_clients()
        targets = [
            client
            for client in clients
            if client.get("workspace", {}).get("id") in workspace_ids
        ]
        if not targets:
            return

        for client in targets:
            addr = client.get("address")
            if addr:
                await dispatch(["closewindow", f"address:{addr}"])
                await asyncio.sleep(0.05)

        await asyncio.sleep(0.2)


async def reconcile_late_windows(saved_windows, delay=4.0, timeout=8.0):
    restored_addresses = {}
    signature_counts = collections.Counter(
        window_signature(sw)
        for sw in saved_windows
        if not is_ambiguous_terminal_window(sw)
        and sw.get("class_name") != "Docker Desktop"
    )
    pending = [
        sw for sw in saved_windows
        if not is_ambiguous_terminal_window(sw)
        and sw.get("class_name") != "Docker Desktop"
        and signature_counts[window_signature(sw)] == 1
    ]
    if not pending:
        return restored_addresses

    await asyncio.sleep(delay)

    attempts = max(1, int(timeout / 0.5))
    for _ in range(attempts):
        if not pending:
            return restored_addresses

        clients = await get_clients()
        next_pending = []
        for sw in pending:
            matches = [client for client in clients if client_matches_saved_window(client, sw)]
            if len(matches) != 1:
                next_pending.append(sw)
                continue

            match = matches[0]
            address = match.get("address")
            if address:
                restored_addresses[id(sw)] = address
            if match.get("workspace", {}).get("id") != sw["workspace_id"]:
                await apply_window_state(sw, match["address"], match.get("fullscreen", 0), move_workspace=True)

        await asyncio.sleep(0.5)
        pending = next_pending

    return restored_addresses


async def find_live_matches(sw, workspace_id=None):
    clients = await get_clients()
    matches = [client for client in clients if client_matches_saved_window(client, sw)]
    if workspace_id is not None:
        matches = [
            client for client in matches
            if client.get("workspace", {}).get("id") == workspace_id
        ]
    return matches


async def close_live_matches(sw, workspace_id=None):
    matches = await find_live_matches(sw, workspace_id=workspace_id)
    closed_addresses = set()
    for match in matches:
        addr = match.get("address")
        if addr:
            closed_addresses.add(addr)
            await dispatch(["closewindow", f"address:{addr}"])
            await asyncio.sleep(0.05)
    if matches:
        await asyncio.sleep(0.5)
    return closed_addresses


async def stabilize_spawned_window(sw, current_client, used_addresses: set):
    observe_window = stabilization_observe_window(sw)
    if observe_window <= 0:
        return current_client

    deadline = asyncio.get_event_loop().time() + observe_window
    latest = current_client
    while asyncio.get_event_loop().time() < deadline:
        matches = await find_live_matches(sw)
        if len(matches) == 1:
            latest = matches[0]
            addr = latest.get("address")
            if addr:
                used_addresses.add(addr)
        await asyncio.sleep(0.1)

    return latest


async def restore_deferred_window(sw, used_addresses, focus_addr=None, preselect=None, adopt_existing=False, force_spawn=False):
    ws_id = sw["workspace_id"]
    trace(f"restore_deferred_window class={sw.get('class_name')} ws={ws_id} focus_addr={focus_addr} preselect={preselect} adopt_existing={adopt_existing} force_spawn={force_spawn}")
    await dispatch(["workspace", str(ws_id)])
    await asyncio.sleep(1.0)

    if focus_addr:
        await focus_window(focus_addr)
        await asyncio.sleep(0.2)
    if preselect:
        await dispatch(["layoutmsg", "preselect", preselect])
        await asyncio.sleep(0.2)

    matches = await find_live_matches(sw, workspace_id=ws_id)
    if adopt_existing and not force_spawn and len(matches) == 1:
        match = matches[0]
        addr = match.get("address")
        if addr:
            used_addresses.add(addr)
            if match.get("workspace", {}).get("id") != ws_id:
                await apply_window_state(sw, addr, match.get("fullscreen", 0), move_workspace=True)
            return addr, sw.get("focus_history_id", 999)

    if matches:
        await close_live_matches(sw, workspace_id=ws_id)

    return await restore_window(sw, [], used_addresses, force_spawn=force_spawn)


def restored_window_groups(saved_windows):
    """Return disjoint ordered groups using snapshot-local window addresses."""
    saved_by_address = {
        sw.get("address"): sw
        for sw in saved_windows
        if sw.get("address")
    }

    candidates = []
    seen = set()
    for sw in saved_windows:
        grouped_addresses = list(dict.fromkeys(sw.get("grouped", [])))
        if not grouped_addresses:
            continue

        # Hyprland currently includes the window itself. Union it defensively
        # so a schema drift or inconsistent snapshot cannot create singletons.
        saved_address = sw.get("address")
        if saved_address and saved_address not in grouped_addresses:
            grouped_addresses.insert(0, saved_address)

        members = []
        for address in grouped_addresses:
            member = saved_by_address.get(address)
            if member is None:
                members = []
                break
            if member not in members:
                members.append(member)

        if not members:
            continue
        if len({member.get("workspace_id") for member in members}) != 1:
            continue

        key = tuple(sorted(member["address"] for member in members))
        if key in seen:
            continue
        seen.add(key)
        candidates.append(members)

    groups = []
    claimed_addresses = set()
    for members in sorted(candidates, key=len, reverse=True):
        addresses = {member["address"] for member in members}
        if claimed_addresses.intersection(addresses):
            continue
        claimed_addresses.update(addresses)
        groups.append(members)

    return groups


def direction_to_window(source, target):
    """Return the Hyprland direction from source toward target."""
    source_at = source.get("at")
    source_size = source.get("size")
    target_at = target.get("at")
    target_size = target.get("size")
    if not source_at or not source_size or not target_at or not target_size:
        return None

    source_center = (
        source_at[0] + source_size[0] / 2,
        source_at[1] + source_size[1] / 2,
    )
    target_center = (
        target_at[0] + target_size[0] / 2,
        target_at[1] + target_size[1] / 2,
    )
    dx = target_center[0] - source_center[0]
    dy = target_center[1] - source_center[1]

    if dx == 0 and dy == 0:
        return None
    if abs(dx) >= abs(dy):
        return "r" if dx > 0 else "l"
    return "d" if dy > 0 else "u"


def warn_group_restore(members, reason):
    labels = [
        member.get("class_name") or member.get("command") or member.get("address", "?")
        for member in members
    ]
    print(
        f"{YELLOW}[!]{RESET} Window group not restored ({reason}): "
        + ", ".join(labels)
    )


async def wait_for_group(addresses, timeout=1.0):
    attempts = max(1, int(timeout / 0.05))
    for _ in range(attempts):
        clients = {client.get("address"): client for client in await get_clients()}
        if all(
            set(clients.get(address, {}).get("grouped", [])) == set(addresses)
            for address in addresses
        ):
            return True
        await asyncio.sleep(0.05)
    return False


async def wait_for_ungrouped(addresses, timeout=1.0):
    attempts = max(1, int(timeout / 0.05))
    for _ in range(attempts):
        clients = {client.get("address"): client for client in await get_clients()}
        if all(not clients.get(address, {}).get("grouped") for address in addresses):
            return True
        await asyncio.sleep(0.05)
    return False


async def rollback_group_attempt(attempted_addresses, original_groups):
    affected_addresses = set(attempted_addresses)
    clients = {client.get("address"): client for client in await get_clients()}

    for address in reversed(attempted_addresses[1:]):
        grouped = clients.get(address, {}).get("grouped", [])
        affected_addresses.update(grouped)
        if not grouped:
            continue
        trace(f"restore_groups rollback-member address={address}")
        await dispatch(["moveoutofgroup", f"address:{address}"])
        await asyncio.sleep(0.05)
        clients = {client.get("address"): client for client in await get_clients()}

    clients = {client.get("address"): client for client in await get_clients()}
    for address in affected_addresses:
        if original_groups.get(address):
            continue
        if clients.get(address, {}).get("grouped") != [address]:
            continue
        if not await focus_window(address):
            continue
        trace(f"restore_groups rollback-singleton address={address}")
        await dispatch(["togglegroup"])
        await wait_for_ungrouped([address])

    clients = {client.get("address"): client for client in await get_clients()}
    return [
        address
        for address in affected_addresses
        if set(clients.get(address, {}).get("grouped", []))
        != set(original_groups.get(address, []))
    ]


async def restore_groups(saved_windows, restored_addresses):
    """Recreate saved window groups after all window placement is complete."""
    for members in restored_window_groups(saved_windows):
        missing_members = [
            member for member in members if not restored_addresses.get(id(member))
        ]
        if missing_members:
            trace(
                "restore_groups incomplete members="
                + ",".join(member.get("address", "") for member in missing_members)
            )
            warn_group_restore(members, "incomplete members")
            continue

        live_addresses = [restored_addresses[id(member)] for member in members]
        clients = {client.get("address"): client for client in await get_clients()}

        already_restored = all(
            set(clients.get(address, {}).get("grouped", [])) == set(live_addresses)
            for address in live_addresses
        )
        active_member = min(
            members,
            key=lambda member: member.get("focus_history_id", 999),
        )
        active_address = restored_addresses[id(active_member)]
        if already_restored:
            await focus_window(active_address)
            continue

        conflicting_addresses = [
            address
            for address in live_addresses
            if clients.get(address, {}).get("grouped")
        ]
        if conflicting_addresses:
            trace(
                "restore_groups conflicting-existing members="
                + ",".join(conflicting_addresses)
            )
            warn_group_restore(members, "members already grouped differently")
            continue

        fullscreen_addresses = [
            address
            for address in live_addresses
            if clients.get(address, {}).get("fullscreen", 0) > 0
        ]
        if fullscreen_addresses:
            trace(
                "restore_groups fullscreen members="
                + ",".join(fullscreen_addresses)
            )
            warn_group_restore(members, "fullscreen members")
            continue

        if len(live_addresses) == 1:
            address = live_addresses[0]
            if await focus_window(address):
                trace(f"restore_groups single address={address}")
                success = await dispatch(["togglegroup"])
                if success is False or not await wait_for_group([address]):
                    warn_group_restore(members, "single-member verification failed")
            continue

        anchor_address = live_addresses[0]
        original_groups = {
            address: list(client.get("grouped", []))
            for address, client in clients.items()
        }
        join_plan = []
        plan_failure = None
        for member_address in live_addresses[1:]:
            source = clients.get(member_address)
            target = clients.get(anchor_address)
            if source is None or target is None:
                trace(
                    f"restore_groups missing-client anchor={anchor_address} "
                    f"member={member_address}"
                )
                join_plan = []
                plan_failure = "missing live client"
                break

            direction = direction_to_window(source, target)
            if direction is None:
                trace(
                    f"restore_groups no-direction anchor={anchor_address} "
                    f"member={member_address}"
                )
                join_plan = []
                plan_failure = "ambiguous window direction"
                break
            join_plan.append((member_address, direction))

        if len(join_plan) != len(live_addresses) - 1:
            warn_group_restore(members, plan_failure or "incomplete join plan")
            continue

        grouped_addresses = [anchor_address]
        for member_address, direction in join_plan:
            if not await focus_window(member_address):
                rollback_failures = await rollback_group_attempt(
                    grouped_addresses,
                    original_groups,
                )
                warn_group_restore(members, "member focus failed")
                if rollback_failures:
                    warn_group_restore(members, "rollback incomplete")
                break

            dispatcher = (
                "moveintogroup"
                if len(grouped_addresses) > 1
                else "moveintoorcreategroup"
            )
            trace(
                f"restore_groups join dispatcher={dispatcher} direction={direction} "
                f"anchor={anchor_address} member={member_address}"
            )
            success = await dispatch([dispatcher, direction])
            if success is False:
                trace(
                    f"restore_groups dispatch-failed dispatcher={dispatcher} "
                    f"member={member_address}"
                )
                rollback_failures = await rollback_group_attempt(
                    [*grouped_addresses, member_address],
                    original_groups,
                )
                warn_group_restore(members, "group dispatcher failed")
                if rollback_failures:
                    warn_group_restore(members, "rollback incomplete")
                break
            await asyncio.sleep(0.05)
            grouped_addresses.append(member_address)
            if not await wait_for_group(grouped_addresses):
                trace(
                    "restore_groups verification-failed members="
                    + ",".join(grouped_addresses)
                )
                rollback_failures = await rollback_group_attempt(
                    grouped_addresses,
                    original_groups,
                )
                warn_group_restore(members, "group verification failed")
                if rollback_failures:
                    warn_group_restore(members, "rollback incomplete")
                break

        if grouped_addresses == live_addresses:
            await focus_window(active_address)


async def restore_session(name="last_session", clean=False):
    session_path = get_session_path(name)

    try:
        with open(session_path, "r") as f:
            saved_windows = json.load(f)
    except FileNotFoundError:
        print(f"{RED}[-]{RESET} Session not found: {session_path}")
        return

    await init_hypr_config()

    workspace_order = []
    grouped_windows = {}
    for sw in saved_windows:
        ws_id = sw["workspace_id"]
        if ws_id not in grouped_windows:
            grouped_windows[ws_id] = []
            workspace_order.append(ws_id)
        grouped_windows[ws_id].append(sw)
    workspace_order.sort()

    if clean:
        await close_windows_on_workspaces(workspace_order)
        matchable_clients = []
    else:
        matchable_clients = await get_clients()

    if TRACE_ENABLED:
        TRACE_PATH.write_text("", encoding="utf-8")
    trace(f"restore_session start name={name} clean={clean} workspaces={workspace_order}")

    used_addresses: set[str] = set()
    focus_candidates = []
    restored_addresses = {}
    deferred_workspace_plans = []
    deferred_steps = []

    for ws_id in workspace_order:
        trace(f"restore_session enter_workspace ws={ws_id}")
        await dispatch(["workspace", str(ws_id)])
        await asyncio.sleep(1.0)

        workspace_windows = grouped_windows[ws_id]
        tiled = [sw for sw in workspace_windows if not sw["is_floating"]]
        floating = [sw for sw in workspace_windows if sw["is_floating"]]

        anchor, steps = build_tiled_restore_plan(tiled)
        if anchor:
            addr, fid = await restore_window(anchor, matchable_clients, used_addresses)
            if not addr:
                deferred_workspace_plans.append(
                    {
                        "workspace_id": ws_id,
                        "anchor": anchor,
                        "steps": steps,
                        "floating": floating,
                    }
                )
                continue
            restored_addresses[id(anchor)] = addr
            if fid is not None:
                focus_candidates.append((addr, fid))

        for step in steps:
            trace(f"restore_session step ws={ws_id} spawn={step['spawn'].get('class_name')} focus={step['focus'].get('class_name')} preselect={step['preselect']}")
            focus_addr = restored_addresses.get(id(step["focus"]))
            if focus_addr:
                await focus_window(focus_addr)
                await asyncio.sleep(0.2)
                if step["preselect"]:
                    await dispatch(["layoutmsg", "preselect", step["preselect"]])
                    await asyncio.sleep(0.2)

            addr, fid = await restore_window(
                step["spawn"],
                matchable_clients,
                used_addresses,
                force_spawn=bool(step["preselect"]),
            )
            if not addr:
                if step["spawn"].get("class_name") != "Docker Desktop":
                    deferred_steps.append(
                        {
                            "workspace_id": ws_id,
                            "focus": step["focus"],
                            "preselect": step["preselect"],
                            "spawn": step["spawn"],
                        }
                    )
                continue
            restored_addresses[id(step["spawn"])] = addr
            if step["preselect"] and needs_tiled_stabilization(step["spawn"]):
                matches = await find_live_matches(
                    step["spawn"],
                    workspace_id=step["spawn"]["workspace_id"],
                )
                if len(matches) == 1 and client_matches_saved_placement(matches[0], step["spawn"]):
                    pass
                else:
                    focus_addr = restored_addresses.get(id(step["focus"]))
                    addr, fid = await restore_deferred_window(
                        step["spawn"],
                        used_addresses,
                        focus_addr=focus_addr,
                        preselect=step["preselect"],
                        adopt_existing=False,
                        force_spawn=True,
                    )
                    if addr:
                        restored_addresses[id(step["spawn"])] = addr
                    if addr and fid is not None:
                        focus_candidates.append((addr, fid))
            if fid is not None:
                focus_candidates.append((addr, fid))

        for sw in floating:
            addr, fid = await restore_window(sw, matchable_clients, used_addresses)
            if not addr:
                deferred_steps.append(
                    {
                        "workspace_id": ws_id,
                        "focus": None,
                        "preselect": None,
                        "spawn": sw,
                    }
                )
                continue
            restored_addresses[id(sw)] = addr
            if fid is not None:
                focus_candidates.append((addr, fid))

    if deferred_workspace_plans or deferred_steps:
        await asyncio.sleep(3.0)

    for plan in deferred_workspace_plans:
        anchor = plan["anchor"]
        addr, fid = await restore_deferred_window(anchor, used_addresses, adopt_existing=True)
        if not addr:
            continue
        restored_addresses[id(anchor)] = addr
        if fid is not None:
            focus_candidates.append((addr, fid))

        for step in plan["steps"]:
            focus_addr = restored_addresses.get(id(step["focus"]))
            addr, fid = await restore_deferred_window(
                step["spawn"],
                used_addresses,
                focus_addr=focus_addr,
                preselect=step["preselect"],
                adopt_existing=False,
            )
            if addr:
                restored_addresses[id(step["spawn"])] = addr
                if step["preselect"] and needs_tiled_stabilization(step["spawn"]):
                    matches = await find_live_matches(
                        step["spawn"],
                        workspace_id=step["spawn"]["workspace_id"],
                    )
                    if not (len(matches) == 1 and client_matches_saved_placement(matches[0], step["spawn"])):
                        addr, fid = await restore_deferred_window(
                            step["spawn"],
                            used_addresses,
                            focus_addr=focus_addr,
                            preselect=step["preselect"],
                            adopt_existing=False,
                            force_spawn=True,
                        )
                        if addr:
                            restored_addresses[id(step["spawn"])] = addr
                        if addr and fid is not None:
                            focus_candidates.append((addr, fid))
            if addr and fid is not None:
                focus_candidates.append((addr, fid))

        for sw in plan["floating"]:
            addr, fid = await restore_deferred_window(sw, used_addresses, adopt_existing=True)
            if addr:
                restored_addresses[id(sw)] = addr
            if addr and fid is not None:
                focus_candidates.append((addr, fid))

    for step in deferred_steps:
        focus_addr = None
        if step["focus"] is not None:
            focus_addr = restored_addresses.get(id(step["focus"]))
        addr, fid = await restore_deferred_window(
            step["spawn"],
            used_addresses,
            focus_addr=focus_addr,
            preselect=step["preselect"],
            adopt_existing=focus_addr is None,
        )
        if addr:
            restored_addresses[id(step["spawn"])] = addr
        if addr and fid is not None:
            focus_candidates.append((addr, fid))

    late_addresses = await reconcile_late_windows(saved_windows)
    if late_addresses:
        restored_addresses.update(late_addresses)
    await restore_groups(saved_windows, restored_addresses)

    if focus_candidates:
        target_addr = min(focus_candidates, key=lambda x: x[1])[0]
        if target_addr:
            trace(f"restore_session final_focus addr={target_addr}")
            await dispatch(["focuswindow", f"address:{target_addr}"])

    trace("restore_session return_workspace ws=1")
    await dispatch(["workspace", "1"])

    print(f"{GREEN}[+]{RESET} Session restored from: {session_path}")


if __name__ == "__main__":
    try:
        asyncio.run(restore_session())
    except KeyboardInterrupt:
        pass
