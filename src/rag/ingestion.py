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
    """将文本切分为合适大小的块

    策略：
    1. 先按双换行（段落）切分
    2. 过短的段落合并
    3. 过长的段落按句号二次切分
    4. 保证每个chunk在min_chunk_size到chunk_size之间

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

    chunks = []
    current_chunk = ""

    for para in paragraphs:
        # 如果当前块+新段落不超过上限，合并
        if len(current_chunk) + len(para) + 2 <= chunk_size:
            if current_chunk:
                current_chunk += "\n\n" + para
            else:
                current_chunk = para
        else:
            # 保存当前块
            if len(current_chunk) >= min_chunk_size:
                chunks.append(current_chunk)
                current_chunk = para
            elif current_chunk:
                # 当前块太短，合并到新段落
                current_chunk += "\n\n" + para
                # 合并后可能超长，按句号切分
                if len(current_chunk) > chunk_size * 1.5:
                    sub_chunks = _split_by_sentences(
                        current_chunk, chunk_size, min_chunk_size
                    )
                    chunks.extend(sub_chunks[:-1])
                    current_chunk = sub_chunks[-1] if sub_chunks else ""
            else:
                current_chunk = para

    # 处理最后一块
    if current_chunk and len(current_chunk) >= min_chunk_size:
        chunks.append(current_chunk)
    elif current_chunk and chunks:
        # 最后一块太短，合并到前一块
        chunks[-1] += "\n\n" + current_chunk

    return chunks


def _split_by_sentences(
    text: str,
    chunk_size: int = 500,
    min_chunk_size: int = 100,
) -> list[str]:
    """按句号切分过长的文本块"""
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
        if len(current) + len(sent) <= chunk_size:
            current += sent
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

    # 提取章节信息
    filename = file_path.name
    chapter_num = _get_chapter_number(filename)
    chapter_str = f"第{chapter_num}章" if chapter_num else file_path.stem
    category = _get_category(chapter_num)
    quantifiability = _get_quantifiability(chapter_num)
    applicable_layers = _get_applicable_layers(chapter_num)

    # 切分为块
    chunks = _split_into_chunks(text, chunk_size, chunk_overlap)

    documents = []
    for i, chunk in enumerate(chunks):
        # 排除心流战法内容
        if _is_flow_state_content(chunk):
            logger.info(f"排除心流战法内容: {chapter_str}_p{i+1}")
            continue

        doc_id = f"ch{chapter_num or '00'}_p{i+1}" if chapter_num else f"{file_path.stem}_p{i+1}"

        doc = RAGDocument(
            id=doc_id,
            content=chunk,
            source_file=str(file_path),
            metadata={
                "chapter": chapter_str,
                "chapter_num": chapter_num,
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
