# -*- coding: utf-8 -*-
"""
lib_parse.py —— 坐标行解析与名称归一化的纯逻辑层
================================================

被 etl.py 使用，也方便单独写测试。本模块不做 IO，不依赖项目结构。

原始数据的三种损伤，都在这里处理：
  1. 两行粘连（缺换行）：x,y,z,名称-123.4,y,z,名称
  2. 缺名称字段：x,y,z（只有 3 段）
  3. 名称里带 ASCII 逗号：x,y,z,名称,甲,乙

解析失败的整行不会被静默丢弃，一律以 Issue 形式返回，由 etl.py 落进 issues.json。
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

import taxonomy as tax

# 坐标合理上限（实测最大 |x|≈4066），超出只标记不丢弃
COORD_ABS_LIMIT = 20000.0
# 名称是占位符、没有信息量的
PLACEHOLDER_NAMES = frozenset({"", "示例", "未备注地点", "未备注", "待补充"})

_NUM = re.compile(r"^-?\d+(?:\.\d+)?$")
# 名称尾部粘了一个坐标：取「最长的数字后缀」
_GLUED_SUFFIX = re.compile(r"^(?P<name>.+?)(?P<x>-?\d+(?:\.\d+)?)$")
_PAREN_NOTE = re.compile(r"[（(]([^）)]*)[)）]\s*$")
_TRAILING_INDEX = re.compile(r"^(?P<base>.*?)(?P<idx>\d+)$")
_WHITESPACE_ROW = re.compile(
    r"^\s*(?P<x>-?\d+(?:\.\d+)?)\s+(?P<y>-?\d+(?:\.\d+)?)\s+(?P<z>-?\d+(?:\.\d+)?)\s+(?P<name>\S.*)?$"
)
# 多余前缀：'3-3862.65'（原始文件里手滑多打了一个「3-」）
_STRAY_PREFIX = re.compile(r"^\d+(-\d+(?:\.\d+)?)$")
# 「清河-露天宝箱」「开封-解密宝箱」这类区域前缀
_REGION_PREFIX = re.compile(r"^(清河|开封|江南|杭州)\s*[-—–]\s*")
_PATTERN_COMPILED = [(cat, re.compile(pat)) for cat, pat in tax.PATTERN_RULES]


# --------------------------------------------------------------------------
# 编码
# --------------------------------------------------------------------------
def decode_bytes(data: bytes) -> tuple[str, str]:
    """返回 (文本, 编码名)。先按 UTF-8 严格解，失败回落 GB18030。"""
    if not data:
        return "", "empty"
    try:
        return data.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        return data.decode("gb18030", errors="replace"), "gb18030"


# --------------------------------------------------------------------------
# 名称归一化
# --------------------------------------------------------------------------
def normalize_key(name: str) -> str:
    """用于去重比对的归一化键：NFKC + 去掉所有空白 + 大小写折叠。

    只影响「判定两条是不是同一条」，不改原始 name。
    """
    s = unicodedata.normalize("NFKC", name)
    s = re.sub(r"\s+", "", s)
    return s.casefold()


def _is_num(s: str) -> bool:
    return bool(_NUM.match(s))


@dataclass
class Issue:
    kind: str
    detail: str
    file: str = ""
    line: int = 0
    raw: str = ""


@dataclass
class Row:
    x: float
    y: float
    z: float
    name: str
    file: str
    line: int
    raw: str
    repairs: list[str] = field(default_factory=list)


def _split_glued(field_text: str) -> tuple[str, str] | None:
    """把「名称-183.437」拆成 ("名称", "-183.437")；拆不开返回 None。

    非贪婪匹配 + 数字锚定行尾 ⇒ 自动取最长的数字后缀，
    于是 "荒魂村传送点2-183.437" → ("荒魂村传送点2", "-183.437")。
    必须要求小数位或足够长的整数，否则 "鸟兽虫鱼2" 这种带序号的名称会被拆坏。
    """
    m = _GLUED_SUFFIX.match(field_text)
    if not m:
        return None
    x = m.group("x")
    name = m.group("name")
    if not name:
        return None
    # 坐标应有小数位或 ≥4 位整数
    if "." not in x and len(x.lstrip("-")) < 4:
        return None
    return name, x


def parse_line(line: str, *, file: str = "", line_no: int = 0) -> tuple[list[Row], list[Issue]]:
    """解析单行，返回 (行列表, 问题列表)。一行可能产出 2 条（粘连修复）。"""
    rows: list[Row] = []
    issues: list[Issue] = []

    text = line.replace("\ufeff", "").strip()
    if not text:
        return rows, issues

    # 段落式 .ini 头（原始数据里没有，但容错）
    if text.startswith("[") and text.endswith("]"):
        issues.append(Issue("section_header", text, file, line_no, line))
        return rows, issues

    if "," in text:
        parts = [p.strip() for p in text.split(",")]
    else:
        m = _WHITESPACE_ROW.match(text)
        if not m:
            issues.append(Issue("unparsed_line", "既非逗号分隔也非空白分隔的数字行", file, line_no, line))
            return rows, issues
        name = (m.group("name") or "").strip()
        return (
            [
                Row(
                    float(m.group("x")), float(m.group("y")), float(m.group("z")),
                    name, file, line_no, line, ["whitespace_delimited"],
                )
            ],
            issues,
        )

    # ---- 修正多余前缀：'3-3862.65' → '-3862.65' --------------------------
    stray = False
    m0 = _STRAY_PREFIX.match(parts[0])
    if m0:
        issues.append(Issue(
            "stray_prefix_fixed", f"首字段多了前缀：{parts[0]} → {m0.group(1)}", file, line_no, line
        ))
        parts[0] = m0.group(1)
        stray = True
    base_repairs = ["stray_prefix"] if stray else []

    # ---- 粘连行：7 段，且 0-2 数字、4-5 数字 ----------------------------
    if len(parts) == 7 and all(_is_num(parts[i]) for i in (0, 1, 2, 4, 5)):
        glued = _split_glued(parts[3])
        if glued:
            name1, x2 = glued
            rows.append(Row(float(parts[0]), float(parts[1]), float(parts[2]),
                            name1, file, line_no, line, base_repairs + ["glued_split"]))
            rows.append(Row(float(x2), float(parts[4]), float(parts[5]),
                            parts[6], file, line_no, line, base_repairs + ["glued_split"]))
            issues.append(Issue(
                "glued_lines_split",
                f"两行粘连，已拆为 2 条：<{name1}> + <{parts[6]}>",
                file, line_no, line,
            ))
            return rows, issues

    # ---- 正常 4 段 ------------------------------------------------------
    if len(parts) >= 4 and all(_is_num(parts[i]) for i in (0, 1, 2)):
        name = ",".join(parts[3:]).strip()
        repairs: list[str] = list(base_repairs)
        if len(parts) > 4:
            repairs.append("name_contains_comma")
            issues.append(Issue("name_contains_comma", f"名称内含 ASCII 逗号：{name}", file, line_no, line))
        rows.append(Row(float(parts[0]), float(parts[1]), float(parts[2]), name, file, line_no, line, repairs))
        return rows, issues

    # ---- 缺名称：3 段 ---------------------------------------------------
    if len(parts) == 3 and all(_is_num(p) for p in parts):
        issues.append(Issue("missing_name", "只有 x,y,z 没有名称", file, line_no, line))
        rows.append(Row(float(parts[0]), float(parts[1]), float(parts[2]), "", file, line_no, line,
                        base_repairs + ["missing_name"]))
        return rows, issues

    issues.append(Issue("unparsed_line", f"字段数={len(parts)}，前 3 段非全数字", file, line_no, line))
    return rows, issues


# --------------------------------------------------------------------------
# 名称语义解析
# --------------------------------------------------------------------------
def classify(name: str) -> str:
    """只要分类结果（兼容旧调用）。"""
    return classify_full(name, "")[0]


def classify_full(name: str, rel_path: str = "") -> tuple[str, str]:
    """返回 (一级分类, 判定依据)。

    判定依据取值：dict 关键词 / pattern 正则 / path 来源路径 / person 人名启发 /
                  placeholder 占位名 / fallback 兜底

    优先级这么排是有理由的：名称是「示例」「未备注地点」这类占位符时（实测 412 条），
    名称本身毫无信息，但它所在的文件往往叫「清河宝箱坐标.ini」—— 路径这时候比名称靠谱。
    """
    n = (name or "").strip()

    # 0. 先把错别字/简写归一，再剥掉「清河-」「开封-」这类区域前缀，
    #    否则 "开封-天涯客" 这种会因为 ^ 锚点匹配不上 "天涯客" 的规则。
    n = tax.ALIASES.get(n, n)
    n = _REGION_PREFIX.sub("", n)

    # 0b. 名称是占位符 → 名称没信息，改看它出自哪个文件
    if n in PLACEHOLDER_NAMES:
        for kw, cat in tax.PATH_CATEGORY:
            if kw in rel_path:
                return cat, "path"
        stem = Path(rel_path).stem
        for m in tax.MATERIALS:
            if m in stem:
                return "材料", "path"
        return "未标注", "placeholder"

    # 1. 关键词
    for cat, keys in tax.CATEGORY_RULES:
        if cat == "未标注":
            continue
        for k in keys:
            if k in n:
                return cat, "dict"

    # 2. 正则模式
    for cat, pat in _PATTERN_COMPILED:
        if pat.search(n):
            return cat, "pattern"

    # 3. 来源路径兜底
    for kw, cat in tax.PATH_CATEGORY:
        if kw in rel_path:
            return cat, "path"
    stem = Path(rel_path).stem
    for m in tax.MATERIALS:
        if m in stem:
            return "材料", "path"

    # 4. 人名启发：2~4 个汉字，且不在停用词里
    if re.match(tax.PERSON_RE, n) and n not in tax.PERSON_STOPWORDS:
        return "NPC", "person"

    # 5. 兜底
    return "其他", "fallback"


def pick_material(name: str) -> str:
    for m in tax.MATERIALS:
        if m in name:
            return tax.ALIASES.get(m, m)
    return tax.ALIASES.get(name.strip(), "")


def pick_place(name: str) -> str:
    for p in tax.PLACES:
        if p in name:
            return p
    return ""


def region_from_name(name: str) -> str:
    """名称里直接出现「清河」「开封」「江南」时，这是比坐标更硬的线索。"""
    for kw, region in tax.REGION_WORDS.items():
        if kw in name:
            return region
    return ""


def split_note(name: str) -> tuple[str, str]:
    """把名称拆成 (主体, 备注)。

    只认两种备注：
      * 括号收尾，如「万事知 鸟兽虫鱼（把信捡起来）」
      * 破折号 + 白名单短语，如「佛泪参慈心山院6-有怪」
    其余一律不动 —— 「开封-露天宝箱」这种不能被拆坏。
    """
    base, note = name, ""
    m = _PAREN_NOTE.search(base)
    if m:
        inner = m.group(1).strip()
        if inner:
            note = inner
            base = base[: m.start()].strip()
    if not note:
        for kw in tax.NOTE_KEYWORDS:
            for sep in ("-", "—", "–"):
                if base.endswith(sep + kw):
                    note = kw
                    base = base[: -(len(kw) + len(sep))].strip()
                    break
            if note:
                break
    return base, note


def split_index(base: str) -> tuple[str, int | None]:
    m = _TRAILING_INDEX.match(base)
    if m and m.group("base"):
        return m.group("base"), int(m.group("idx"))
    return base, None


def region_from_path(rel_path: str) -> str:
    for key, region in tax.REGION_BY_PATH.items():
        if key in rel_path:
            return region
    return ""


def region_from_coord(x: float, y: float) -> str:
    """坐标兜底判区。

    实测：清河 X[-4048,-2169] Y[-2558,-180]；开封 X[-2730,418] Y[-1079,1515]。
    两区在 X∈[-2730,-2169] 有窄重叠，落在重叠带里就返回空（宁可不判错）。
    """
    if x < -2730.0:
        return "清河"
    if x > -2169.0:
        return "开封"
    return ""


def layer_of(z: float) -> str:
    """按高度分层。实测地表主体在 Z∈[-200, 100]，鬼市等在 -750 一档。"""
    if z < -400:
        return "深层地下"
    if z < -150:
        return "地下"
    if z > 120:
        return "高台"
    return "地表"
