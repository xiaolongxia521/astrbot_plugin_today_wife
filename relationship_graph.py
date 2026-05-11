import asyncio
import math
import os
from typing import Dict, List, Optional, Set, Tuple

import aiohttp
import networkx as nx
from PIL import Image, ImageDraw, ImageFont

from astrbot.api import logger

_AVATAR_URL = "https://q1.qlogo.cn/g?b=qq&nk={qq}&s=640"

_CJK_FONT_URL = "https://github.com/googlefonts/noto-cjk/raw/main/Sans/OTF/SimplifiedChinese/NotoSansSC-Regular.otf"
_CJK_FONT_FILENAME = "NotoSansSC-Regular.otf"

_PLUGIN_FONT_DIR_NAME = "fonts"

_SYSTEM_FONT_PATHS = [
    "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf",
    "/usr/share/fonts/truetype/arphic/uming.ttc",
    "/System/Library/Fonts/PingFang.ttc",
    "C:\\Windows\\Fonts\\msyh.ttc",
    "C:\\Windows\\Fonts\\simhei.ttf",
]

_cached_font_dir: Optional[str] = None


async def ensure_cjk_font(cache_dir: str) -> str:
    global _cached_font_dir
    font_dir = os.path.join(cache_dir, _PLUGIN_FONT_DIR_NAME)
    font_path = os.path.join(font_dir, _CJK_FONT_FILENAME)
    if os.path.exists(font_path):
        _cached_font_dir = font_dir
        return font_dir
    for sys_path in _SYSTEM_FONT_PATHS:
        if os.path.exists(sys_path):
            _cached_font_dir = font_dir
            return font_dir
    logger.info(f"未找到系统中文字体，正在下载 {_CJK_FONT_FILENAME} 到 {font_dir}...")
    os.makedirs(font_dir, exist_ok=True)
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                _CJK_FONT_URL,
                timeout=aiohttp.ClientTimeout(total=120),
            ) as resp:
                if resp.status == 200:
                    data = await resp.read()
                    with open(font_path, "wb") as f:
                        f.write(data)
                    logger.info(f"中文字体下载成功，大小: {len(data)} bytes")
                    _cached_font_dir = font_dir
                    return font_dir
                else:
                    logger.warning(f"中文字体下载失败，HTTP状态码: {resp.status}")
    except Exception as e:
        logger.warning(f"中文字体下载失败: {e}")
    _cached_font_dir = font_dir
    return font_dir


async def download_avatar(
    qq: str, cache_dir: str, session: aiohttp.ClientSession
) -> Optional[str]:
    avatar_path = os.path.join(cache_dir, f"{qq}.jpg")
    if os.path.exists(avatar_path):
        return avatar_path
    try:
        async with session.get(
            _AVATAR_URL.format(qq=qq),
            timeout=aiohttp.ClientTimeout(total=5),
        ) as resp:
            if resp.status == 200:
                data = await resp.read()
                if len(data) > 100:
                    os.makedirs(cache_dir, exist_ok=True)
                    with open(avatar_path, "wb") as f:
                        f.write(data)
                    return avatar_path
    except Exception as e:
        logger.warning(f"下载头像失败 QQ={qq}: {e}")
    return None


def _make_circle_avatar(img_path: str, size: int) -> Optional[Image.Image]:
    try:
        img = Image.open(img_path).convert("RGBA")
        img = img.resize((size, size), Image.LANCZOS)
        mask = Image.new("L", (size, size), 0)
        ImageDraw.Draw(mask).ellipse((0, 0, size - 1, size - 1), fill=255)
        out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        out.paste(img, (0, 0), mask)
        return out
    except Exception as e:
        logger.warning(f"创建圆形头像失败: {e}")
        return None


def _make_fallback_avatar(size: int) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    ImageDraw.Draw(img).ellipse((0, 0, size - 1, size - 1), fill=(189, 195, 199, 255))
    return img


def extract_relationships(
    daily_marriages: Dict, group_id: str
) -> Tuple[List[Tuple[str, str]], Set[frozenset]]:
    if group_id not in daily_marriages:
        return [], set()
    edges: List[Tuple[str, str]] = []
    seen: Set[Tuple[str, str]] = set()
    for uid, wife_list in daily_marriages[group_id].items():
        for wid in wife_list:
            u, w = str(uid), str(wid)
            if u == w:
                continue
            e = (u, w)
            if e not in seen:
                edges.append(e)
                seen.add(e)
    bi: Set[frozenset] = set()
    for u, v in edges:
        if (v, u) in seen:
            bi.add(frozenset([u, v]))
    return edges, bi


def _get_font(size: int) -> Tuple[object, bool]:
    search_paths = []
    if _cached_font_dir:
        search_paths.append(os.path.join(_cached_font_dir, _CJK_FONT_FILENAME))
    try:
        from astrbot.core.utils.astrbot_path import get_astrbot_data_path
        search_paths.append(os.path.join(get_astrbot_data_path(), "font.ttf"))
    except Exception:
        pass
    search_paths.extend(_SYSTEM_FONT_PATHS)
    for p in search_paths:
        if not p:
            continue
        if not os.path.exists(p):
            continue
        try:
            return ImageFont.truetype(p, size), True
        except Exception:
            continue
    try:
        return ImageFont.load_default(size=size), False
    except TypeError:
        return ImageFont.load_default(), False


def _draw_arrowhead(
    draw: ImageDraw.Draw,
    tip: Tuple[float, float],
    ux: float,
    uy: float,
    color: str,
    head_size: int = 8,
):
    px, py = -uy, ux
    p1 = (
        tip[0] - head_size * ux + head_size * 0.4 * px,
        tip[1] - head_size * uy + head_size * 0.4 * py,
    )
    p2 = (
        tip[0] - head_size * ux - head_size * 0.4 * px,
        tip[1] - head_size * uy - head_size * 0.4 * py,
    )
    draw.polygon([tip, p1, p2], fill=color)


def render_graph(
    edges: List[Tuple[str, str]],
    bidirectional: Set[frozenset],
    nickname_map: Dict[str, str],
    avatar_paths: Dict[str, Optional[str]],
    output_path: str,
    title: Optional[str] = None,
) -> bool:
    if not edges:
        return False

    nodes: Set[str] = set()
    for u, v in edges:
        nodes.add(u)
        nodes.add(v)
    n = len(nodes)

    avatar_sz = max(40, min(80, 600 // max(n, 1)))
    cw = max(1200, min(3000, n * 120))
    ch = max(800, min(2000, n * 80))
    pad = avatar_sz + 50

    font_sz = max(10, min(16, avatar_sz // 5))
    font, cn_ok = _get_font(font_sz)
    sfont, _ = _get_font(max(8, font_sz - 2))
    tfont, _ = _get_font(max(14, font_sz + 4))

    G = nx.DiGraph()
    G.add_nodes_from(nodes)
    G.add_edges_from(edges)
    try:
        k = max(1.5, 5.0 / math.sqrt(max(n, 1)))
        pos = nx.spring_layout(G, k=k, iterations=100, seed=42)
    except Exception:
        pos = nx.circular_layout(G)

    if len(pos) > 1:
        xs = [p[0] for p in pos.values()]
        ys = [p[1] for p in pos.values()]
        xr = max(xs) - min(xs) or 1
        yr = max(ys) - min(ys) or 1
        sp = {}
        for nd, (x, y) in pos.items():
            sp[nd] = (
                pad + (cw - 2 * pad) * (x - min(xs)) / xr,
                pad + (ch - 2 * pad) * (y - min(ys)) / yr,
            )
    else:
        nd = next(iter(pos))
        sp = {nd: (cw / 2, ch / 2)}

    canvas = Image.new("RGBA", (cw, ch), (245, 245, 245, 255))
    draw = ImageDraw.Draw(canvas)

    if title:
        try:
            bb = draw.textbbox((0, 0), title, font=tfont)
            tw = bb[2] - bb[0]
            draw.text(((cw - tw) / 2, 15), title, fill=(60, 60, 60, 255), font=tfont)
        except Exception:
            pass

    drawn_bi: Set[frozenset] = set()
    for u, v in edges:
        pair = frozenset([u, v])
        is_bi = pair in bidirectional
        if is_bi and pair in drawn_bi:
            continue
        if is_bi:
            color, w = "#E74C3C", 3
            drawn_bi.add(pair)
        else:
            color, w = "#7F8C8D", 2

        sx, sy = sp[u]
        ex, ey = sp[v]
        dx, dy = ex - sx, ey - sy
        length = math.hypot(dx, dy)
        if length < 1:
            continue
        ux_d, uy_d = dx / length, dy / length
        off = avatar_sz / 2 + 5
        ls = (sx + off * ux_d, sy + off * uy_d)
        le = (ex - off * ux_d, ey - off * uy_d)

        draw.line([ls, le], fill=color, width=w)

        if is_bi:
            _draw_arrowhead(draw, le, ux_d, uy_d, color, 10)
            _draw_arrowhead(draw, ls, -ux_d, -uy_d, color, 10)
        else:
            _draw_arrowhead(draw, le, ux_d, uy_d, color, 8)

    half = avatar_sz // 2
    for nid in nodes:
        x, y = sp[nid]
        aimg = None
        ap = avatar_paths.get(nid)
        if ap:
            aimg = _make_circle_avatar(ap, avatar_sz)
        if aimg is None:
            aimg = _make_fallback_avatar(avatar_sz)

        bsz = avatar_sz + 6
        border = Image.new("RGBA", (bsz, bsz), (0, 0, 0, 0))
        ImageDraw.Draw(border).ellipse((0, 0, bsz - 1, bsz - 1), fill=(255, 255, 255, 255))
        canvas.paste(border, (int(x - bsz // 2), int(y - bsz // 2)), border)
        canvas.paste(aimg, (int(x - half), int(y - half)), aimg)

        nick = nickname_map.get(nid, nid)
        try:
            bb = draw.textbbox((0, 0), nick, font=font)
            tw = bb[2] - bb[0]
        except Exception:
            tw = len(nick) * font_sz
        tx = x - tw / 2
        ty = y + half + 6
        try:
            bb2 = draw.textbbox((tx, ty), nick, font=font)
            draw.rectangle(
                [bb2[0] - 2, bb2[1] - 2, bb2[2] + 2, bb2[3] + 2],
                fill=(255, 255, 255, 180),
            )
        except Exception:
            pass
        draw.text((tx, ty), nick, fill=(50, 50, 50, 255), font=font)

    ly = 20
    lx = 20
    draw.rectangle(
        [lx - 5, ly - 5, lx + 250, ly + 55], fill=(255, 255, 255, 220)
    )
    draw.line([(lx, ly + 10), (lx + 30, ly + 10)], fill="#7F8C8D", width=2)
    draw.text((lx + 35, ly + 3), "单向老婆关系", fill=(80, 80, 80), font=sfont)
    draw.line([(lx, ly + 35), (lx + 30, ly + 35)], fill="#E74C3C", width=3)
    draw.text((lx + 35, ly + 28), "双向老婆关系", fill=(80, 80, 80), font=sfont)

    dir_path = os.path.dirname(output_path)
    if dir_path:
        os.makedirs(dir_path, exist_ok=True)
    canvas.convert("RGB").save(output_path, "PNG")
    return True


async def generate_relationship_graph(
    daily_marriages: Dict,
    group_id: str,
    nickname_map: Dict[str, str],
    cache_dir: str,
    output_path: str,
    title: Optional[str] = None,
) -> Optional[str]:
    edges, bi = extract_relationships(daily_marriages, group_id)
    if not edges:
        return None

    nodes: Set[str] = set()
    for u, v in edges:
        nodes.add(u)
        nodes.add(v)

    avdir = os.path.join(cache_dir, "avatars")
    os.makedirs(avdir, exist_ok=True)

    apaths: Dict[str, Optional[str]] = {}
    async with aiohttp.ClientSession() as session:
        tasks = [download_avatar(qq, avdir, session) for qq in nodes]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for qq, r in zip(nodes, results):
            if isinstance(r, Exception):
                logger.warning(f"头像下载异常 QQ={qq}: {r}")
                apaths[qq] = None
            elif isinstance(r, str):
                apaths[qq] = r
            else:
                apaths[qq] = None

    ok = render_graph(edges, bi, nickname_map, apaths, output_path, title)
    return output_path if ok else None
