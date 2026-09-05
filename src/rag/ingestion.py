"""RAG数据摄入 - 加载策略txt文件并分块 (v0.8.1)

数据流：原始txt → 清洗分块 → 元数据标注 → RAGDocument列表

分块策略：
- 每个txt文件按自然段落切分
- 段落长度300-800字
- 超长段落按句号二次切分
- 过短段落(<100字)合并到下一段
- 排除心流战法相关内容
"""

import re
import logging
from pathlib import Path
from typing import Optional

from src.rag.models import RAGDocument

logger = logging.getLogger(__name__)

# 心流战法关键词（超验/主观意识流内容，不入库）
FLOW_STATE_KEYWORDS = [
    "心流状态", "zone状态", "直觉交易", "冥想入定",
    "超意识", "灵性交易", "禅定交易", "气功交易",
]

# 章节→分类映射（基于策略文件内容分析）
CHAPTER_CATEGORY_MAP = {
    # 第1-11章: 技术分析基础/拆穿神话
    range(1, 12): "technical",
    # 第13-20章: 技术指标/基本面
    range(13, 21): "technical",
    # 第21-30章: 交易本质/技术方法
    range(21, 31): "technical",
    # 第31-40章: 技术分析进阶/回测
    range(31, 41): "technical",
    # 第41-47章: 市场环境/主力行为
    range(41, 48): "market_analysis",
    # 第48-50章: 止损/止盈/风控
    range(48, 51): "risk_management",
    # 第51-65章: 投资心理
    range(51, 66): "psychology",
}

# 章节→可量化程度映射
CHAPTER_QUANTIFIABILITY_MAP = {
    range(1, 12): "low",         # 拆穿神话类，理念为主
    range(13, 21): "medium",     # 技术指标，部分可量化
    range(21, 31): "high",       # 交易方法，规则清晰
    range(31, 41): "high",       # 回测/方法论，可量化
    range(41, 48): "medium",     # 市场环境，部分可量化
    range(48, 51): "high",       # 止损止盈，规则明确
    range(51, 66): "low",        # 心理学，难以量化
}

# 章节→适用系统层映射
CHAPTER_LAYERS_MAP = {
    range(1, 12): ["Chat"],                           # 理念类，Chat参考
    range(13, 21): ["Decision", "Chat"],               # 技术指标
    range(21, 31): ["Decision", "Strategy", "Chat"],   # 交易方法
    range(31, 41): ["Decision", "Chat"],               # 方法论
    range(41, 48): ["Decision", "AI_Modifier", "Chat"],# 市场环境
    range(48, 51): ["Strategy", "Execution", "Chat"],  # 风控
    range(51, 66): ["AI_Modifier", "Chat"],             # 心理学
}

# v0.8.8.8 系列隔离：知识库不再是单一「第N章」序列，而是 4 个并行系列。
# 若不加系列前缀，「我的金融战争第10章」与「第10章 融资盘」会解析出相同 doc_id
# → store._id_to_idx 字典覆盖 → 其中一半文档被静默屏蔽（实测 91 个 ID / 182 块冲突）。
SERIES_ID_PREFIX = {
    "cognition": "rz",   # 认知革命（第N章）
    "war": "jrz",        # 我的金融战争（操盘篇第N章，纪实小说）
    "qianpian": "qp",    # 交易千篇之N
    "outline": "ol",     # 大纲类（望周知等）
    "other": "ot",       # 其他（子目录 .md 等）
}

# 认知革命 66-83 章：51-65 心理篇的延续（热手谬误/情绪记录/认知失调…）
COGNITION_EXT_CATEGORY_MAP = {range(66, 84): "psychology"}
COGNITION_EXT_QUANT_MAP = {range(66, 84): "low"}
COGNITION_EXT_LAYERS_MAP = {range(66, 84): ["AI_Modifier", "Chat"]}

# 交易千篇按主题分段（编号不连续：84-99 / 101-105 / 451-459）
QIANPIAN_CATEGORY_MAP = {
    range(84, 100): "psychology",        # 情绪管理与纪律
    range(101, 106): "risk_management",  # 止损系列
    range(451, 460): "market_analysis",  # 熊市三段 / 熊市怎么活
}
QIANPIAN_QUANT_MAP = {
    range(84, 100): "low",
    range(101, 106): "high",             # 止损规则可直接编码
    range(451, 460): "medium",
}
QIANPIAN_LAYERS_MAP = {
    range(84, 100): ["AI_Modifier", "Chat"],
    range(101, 106): ["Strategy", "Execution", "Chat"],
    range(451, 460): ["Decision", "AI_Modifier", "Chat"],
}


def _detect_series(filename: str) -> str:
    """按文件名判定所属系列

    Args:
        filename: 文件名（不含路径）

    Returns:
        cognition / war / qianpian / outline / other
    """
    if filename.startswith("我的金融战争"):
        return "war"
    if filename.startswith("交易千篇"):
        return "qianpian"
    if filename.startswith("望周知"):
        return "outline"
    if re.match(r"^第\d+章", filename):
        return "cognition"
    return "other"


def _get_series_number(filename: str, series: str) -> Optional[int]:
    """提取系列内编号（章号/篇号）

    Args:
        filename: 文件名
        series: _detect_series 的返回值

    Returns:
        编号；合刊（如451-455）返回起始号；无编号返回 None
    """
    if series in ("cognition", "war"):
        m = re.search(r"第(\d+)章", filename)
        return int(m.group(1)) if m else None
    if series == "qianpian":
        m = re.search(r"交易千篇之?(\d+)", filename)
        return int(m.group(1)) if m else None
    return None


def _get_chapter_number(filename: str) -> Optional[int]:
    """从文件名提取章节号

    Args:
        filename: 文件名（不含路径）

    Returns:
        章节号或None（如大纲文件）
    """
    match = re.search(r'第(\d+)章', filename)
    if match:
        return int(match.group(1))
    return None


def _get_category(chapter: Optional[int]) -> str:
    """根据章节号获取分类"""
    if chapter is None:
        return "market_analysis"  # 大纲等文件
    for rng, cat in CHAPTER_CATEGORY_MAP.items():
        if chapter in rng:
            return cat
    return "technical"


def _get_quantifiability(chapter: Optional[int]) -> str:
    """根据章节号获取可量化程度"""
    if chapter is None:
        return "medium"
    for rng, q in CHAPTER_QUANTIFIABILITY_MAP.items():
        if chapter in rng:
            return q
    return "medium"


def _get_applicable_layers(chapter: Optional[int]) -> list[str]:
    """根据章节号获取适用系统层"""
    if chapter is None:
        return ["Chat"]
    for rng, layers in CHAPTER_LAYERS_MAP.items():
        if chapter in rng:
            return layers
    return ["Chat"]


def _lookup_range(mapping: dict, num: Optional[int], default):
    """在 range 键映射表中查值"""
    if num is None:
        return default
    for rng, val in mapping.items():
        if num in rng:
            return val
    return default


def _resolve_metadata(
    filename: str,
    series: str,
    num: Optional[int],
    fallback_stem: str,
) -> tuple[str, str, str, list[str]]:
    """按系列解析 (chapter显示名, category, quantifiability, applicable_layers)

    认知革命沿用历史章节映射；其余系列走各自的主题映射，
    避免新系列全部落进 market_analysis / Chat 的默认值。
    """
    if series == "cognition":
        chapter_str = f"第{num}章" if num else fallback_stem
        # 66 章之后是心理篇延伸，历史映射表未覆盖
        category = _lookup_range(COGNITION_EXT_CATEGORY_MAP, num, None) \
            or _get_category(num)
        quant = _lookup_range(COGNITION_EXT_QUANT_MAP, num, None) \
            or _get_quantifiability(num)
        layers = _lookup_range(COGNITION_EXT_LAYERS_MAP, num, None) \
            or _get_applicable_layers(num)
        return chapter_str, category, quant, layers

    if series == "war":
        # 纪实小说：内容与真实盘面叙事强相关，但不可驱动决策，
        # 限定 Chat 层，避免小说情节进入 Decision/Strategy 链路。
        chapter_str = f"金融战争第{num:02d}章" if num else f"金融战争·{fallback_stem}"
        return chapter_str, "market_analysis", "low", ["Chat"]

    if series == "qianpian":
        chapter_str = f"交易千篇之{num}" if num else fallback_stem
        category = _lookup_range(QIANPIAN_CATEGORY_MAP, num, "market_analysis")
        quant = _lookup_range(QIANPIAN_QUANT_MAP, num, "medium")
        layers = _lookup_range(QIANPIAN_LAYERS_MAP, num, ["Chat"])
        return chapter_str, category, quant, layers

    if series == "outline":
        return fallback_stem, "market_analysis", "medium", ["Chat"]

    # other：子目录下的 .md（笨总教学等）与未归类文件
    return fallback_stem, "market_analysis", "medium", ["Chat"]


def _is_flow_state_content(text: str) -> bool:
    """检查文本是否属于心流战法内容（应排除）"""
    for keyword in FLOW_STATE_KEYWORDS:
        if keyword in text:
            return True
    return False


def _split_into_chunks(
    text: str,
    chunk_size: int = 500,
    chunk_overlap: int = 50,
    min_chunk_size: int = 100,
) -> list[str]:
    """将文本切分为合适大小的块（v0.8.9.0 修复 P2-A/P2-B）

    策略：
    1. 先按双换行（段落）切分
    2. 超过 chunk_size 的段落先按句硬切（P2-B：杜绝 bge 512 token 截断）
    3. 未超限的段落合并；闭块时携带前块尾部 chunk_overlap 字（P2-A：实装重叠）
    4. 硬顶 chunk_size + chunk_overlap（默认 550 字）

    Args:
        text: 输入文本
        chunk_size: 目标块大小（字）
        chunk_overlap: 块重叠（字）
        min_chunk_size: 最小块大小

    Returns:
        文本块列表
    """
    # 按段落切分
    paragraphs = re.split(r'\n\s*\n', text)
    paragraphs = [p.strip() for p in paragraphs if p.strip()]

    if not paragraphs:
        return []

    # P2-B：超长段落先按句硬切，保证进入合并环节的片段不超过 chunk_size
    pieces: list[str] = []
    for para in paragraphs:
        if len(para) > chunk_size:
            pieces.extend(
                _split_by_sentences(para, chunk_size, min_chunk_size, chunk_overlap)
            )
        else:
            pieces.append(para)

    chunks: list[str] = []
    current = ""

    for piece in pieces:
        if not current:
            current = piece
            continue
        # 未超上限则合并
        if len(current) + len(piece) + 2 <= chunk_size:
            current = current + "\n\n" + piece
            continue
        # 闭块：携带前块尾部 overlap 字符，保证跨块上下文连续（P2-A）
        chunks.append(current)
        if chunk_overlap > 0:
            # 重叠不能让新块超过硬顶 chunk_size + chunk_overlap
            headroom = chunk_size + chunk_overlap - len(piece) - 2
            tail_len = min(chunk_overlap, headroom) if headroom > 0 else 0
            if tail_len > 0:
                current = current[-tail_len:] + "\n\n" + piece
                continue
        current = piece

    # 处理最后一块
    if current:
        if len(current) >= min_chunk_size or not chunks:
            chunks.append(current)
        else:
            merged = chunks[-1] + "\n\n" + current
            if len(merged) <= chunk_size + chunk_overlap:
                chunks[-1] = merged
            else:
                # 并入会破硬顶：短尾独立成块（宁短勿超）
                chunks.append(current)

    return chunks


def _split_by_sentences(
    text: str,
    chunk_size: int = 500,
    min_chunk_size: int = 100,
    chunk_overlap: int = 0,
) -> list[str]:
    """按句号切分过长的文本块

    v0.8.9.0: 新增单句超限硬切（无标点长句按滑窗切分），内容不丢失。
    """
    # 中文句号、问号、感叹号、分号
    sentences = re.split(r'([。！？；])', text)

    # 重新组合句子（保留标点）
    combined = []
    i = 0
    while i < len(sentences):
        s = sentences[i]
        if i + 1 < len(sentences) and sentences[i + 1] in '。！？；':
            s += sentences[i + 1]
            i += 2
        else:
            i += 1
        if s.strip():
            combined.append(s.strip())

    # 组合为合适大小的块
    chunks = []
    current = ""
    for sent in combined:
        if len(sent) > chunk_size:
            # 单句超限（无标点长句）：按滑窗硬切，步长带 overlap，不丢内容
            if current:
                chunks.append(current)
                current = ""
            step = max(chunk_size - chunk_overlap, 1)
            start = 0
            while start < len(sent):
                part = sent[start:start + chunk_size]
                if len(part) >= min_chunk_size or start + chunk_size >= len(sent):
                    chunks.append(part)
                start += step
            continue
        if len(current) + len(sent) <= chunk_size:
            current = current + sent
        else:
            if current and len(current) >= min_chunk_size:
                chunks.append(current)
            current = sent

    if current:
        chunks.append(current)

    return chunks if chunks else [text]


def load_strategy_files(
    knowledge_dir: str,
    chunk_size: int = 500,
    chunk_overlap: int = 50,
) -> list[RAGDocument]:
    """加载策略目录下所有txt文件，切分为RAGDocument

    Args:
        knowledge_dir: 策略文件目录路径
        chunk_size: 块大小
        chunk_overlap: 块重叠

    Returns:
        RAGDocument列表
    """
    dir_path = Path(knowledge_dir)
    if not dir_path.exists():
        logger.warning(f"策略目录不存在: {knowledge_dir}")
        return []

    documents = []
    # v0.8.6.2: rglob 递归扫子目录 + 同时支持 .txt / .md / .html 三种格式
    # 为了让 投资策略（持续更新）/笨总教学.../*.md 也能进 RAG
    txt_files = sorted(
        list(dir_path.rglob("*.txt"))
        + list(dir_path.rglob("*.md"))
    )

    if not txt_files:
        logger.warning(f"策略目录下无 txt/md 文件: {knowledge_dir}")
        return []

    logger.info(f"开始摄入策略文件: {len(txt_files)}个文件（含 .txt 和 .md）")

    for txt_file in txt_files:
        try:
            file_docs = _process_single_file(
                txt_file, chunk_size, chunk_overlap
            )
            documents.extend(file_docs)
        except Exception as e:
            logger.error(f"处理文件失败 {txt_file.name}: {e}")

    logger.info(f"策略摄入完成: {len(documents)}个文档块")
    return documents


def _process_single_file(
    file_path: Path,
    chunk_size: int,
    chunk_overlap: int,
) -> list[RAGDocument]:
    """处理单个策略txt文件

    Args:
        file_path: 文件路径
        chunk_size: 块大小
        chunk_overlap: 块重叠

    Returns:
        该文件的RAGDocument列表
    """
    # 读取文件
    with open(file_path, 'r', encoding='utf-8') as f:
        text = f.read().strip()

    if not text:
        return []

    # 提取系列与章节信息（v0.8.8.8：系列感知，消除跨系列 doc_id 冲突）
    filename = file_path.name
    series = _detect_series(filename)
    chapter_num = _get_series_number(filename, series)
    chapter_str, category, quantifiability, applicable_layers = _resolve_metadata(
        filename, series, chapter_num, file_path.stem
    )

    # 切分为块
    chunks = _split_into_chunks(text, chunk_size, chunk_overlap)

    documents = []
    for i, chunk in enumerate(chunks):
        # 排除心流战法内容
        if _is_flow_state_content(chunk):
            logger.info(f"排除心流战法内容: {chapter_str}_p{i+1}")
            continue

        # doc_id 必须全局唯一：系列前缀 + 系列内编号
        prefix = SERIES_ID_PREFIX.get(series, "ot")
        if chapter_num:
            doc_id = f"{prefix}{chapter_num:02d}_p{i+1}"
        else:
            safe_stem = re.sub(r"[^\w一-鿿-]", "_", file_path.stem)
            doc_id = f"{prefix}_{safe_stem}_p{i+1}"

        doc = RAGDocument(
            id=doc_id,
            content=chunk,
            source_file=str(file_path),
            metadata={
                "chapter": chapter_str,
                "chapter_num": chapter_num,
                "series": series,
                "category": category,
                "quantifiability": quantifiability,
                "applicable_layers": applicable_layers,
                "chunk_index": i + 1,
                "total_chunks": len(chunks),
                "file_name": filename,
            }
        )
        documents.append(doc)

    return documents
