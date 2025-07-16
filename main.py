import re
import tempfile
from io import StringIO
import argparse
import gradio as gr
import pandas as pd
from typing import List

APP_TITLE = "QCA / NCA 结果整理工具箱"
ENC = "utf-8-sig"           # 让 Excel 打开 CSV 时中文不乱码

# --------------------------------------------------
# 通用工具
# --------------------------------------------------
def robust_search(pattern: str, text: str, n_groups: int = 1, default=None):
    m = re.search(pattern, text)
    if m:
        return m.groups() if n_groups > 1 else m.group(1)
    return default

def df_to_tsv(df: pd.DataFrame) -> str:
    """DataFrame → 制表符文本，可直接粘贴进 Excel"""
    return df.to_csv(sep="\t", index=False, encoding="utf-8")

# --------------------------------------------------
# fsQCA 必要性解析（仅文本方式）
# --------------------------------------------------
def _block_to_df(block: str) -> pd.DataFrame:
    df = pd.read_csv(StringIO(block.strip()), sep=r"\s+", header=None)
    if df.shape[1] == 5:
        df = df.drop(columns=0)        # 去掉序号列
    if df.shape[1] != 4:
        raise ValueError("每行应为: 条件 inclN RoN covN")
    df.columns = ["条件", "inclN", "RoN", "covN"]
    return df.set_index("条件").astype(float)

def parse_fs_text(raw_text: str) -> pd.DataFrame:
    parts = re.split(r"\s*inclN\s+RoN\s+covN\s*", raw_text.strip())
    if len(parts) < 3:
        raise ValueError("数据格式错误：需包含两处 “inclN RoN covN” 标题。")
    df_pos = _block_to_df(parts[1])
    df_neg = _block_to_df(parts[2])
    combined = pd.concat([df_pos, df_neg])

    ordered = []
    for cond in df_pos.index:
        ordered.append(cond)
        neg = "~" + cond if not cond.startswith("~") else cond[1:]
        if neg in combined.index:
            ordered.append(neg)
    combined = combined.loc[ordered]

    return (combined.reset_index()
            .rename(columns={"index": "条件变量",
                             "inclN": "一致性",
                             "RoN": "原始覆盖率",
                             "covN": "唯一覆盖率"}))

# --------------------------------------------------
# NCA 解析
# --------------------------------------------------
def parse_nca(raw: str) -> pd.DataFrame:
    parts = re.split(r"NCA Parameters\s*:\s*(.*?)\s*-\s*\S+", raw)
    rows, order = [], []
    for i in range(1, len(parts), 2):
        cond, block = parts[i], parts[i + 1]
        if cond not in order:
            order.append(cond)

        scope_val = robust_search(r"Scope\s+([0-9.]+)", block)
        if scope_val is None:
            continue
        scope = float(scope_val)
        up_zone   = robust_search(r"Ceiling zone\s+([0-9.]+)\s+([0-9.]+)", block, 2, ("N/A","N/A"))
        eff_size  = robust_search(r"Effect size\s+([0-9.]+)\s+([0-9.]+)", block, 2, ("N/A","N/A"))
        accuracy  = robust_search(r"c-accuracy\s+([0-9.]+%)\s+([0-9.]+%)", block, 2, ("N/A","N/A"))
        p_vals    = robust_search(r"p-value\s+([0-9.]+)\s+([0-9.]+)", block, 2, ("N/A","N/A"))
        for idx, m in enumerate(["ce_fdh", "cr_fdh"]):
            rows.append({"条件": cond, "方法": m,
                         "精确度":    accuracy[idx],
                         "上限区域":  up_zone[idx],
                         "范围":      scope,
                         "效应量(d)": eff_size[idx],
                         "P值":      p_vals[idx]})
    df = pd.DataFrame(rows)
    df["条件"] = pd.Categorical(df["条件"], categories=order, ordered=True)
    df["方法"] = pd.Categorical(df["方法"], ["ce_fdh", "cr_fdh"], ordered=True)
    return df.sort_values(["条件", "方法"])

# --------------------------------------------------
# QCA 组态 → 表
# --------------------------------------------------
def qca_to_df(cfg: str, sym_p="●", sym_a="⊗", sym_n="") -> pd.DataFrame:
    configs = [c.strip() for c in cfg.split("+") if c.strip()]
    labels  = [f"组态{i+1}" for i in range(len(configs))]
    order, maps = [], []

    for conf in configs:
        mp = {}
        for tok in [t.strip() for t in conf.split("*") if t.strip()]:
            var, sym = (tok[1:], sym_a) if tok.startswith("~") else (tok, sym_p)
            mp[var] = sym
            if var not in order:
                order.append(var)
        maps.append(mp)

    df = pd.DataFrame(index=order, columns=labels)
    for i, mp in enumerate(maps):
        for v, s in mp.items():
            df.loc[v, labels[i]] = s
    df.fillna(sym_n, inplace=True)
    return df.reset_index().rename(columns={"index": "条件变量"})

# --------------------------------------------------
# Gradio 包装
# --------------------------------------------------
def _return_outputs(df: pd.DataFrame):
    csv_tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".csv", mode="w", encoding=ENC)
    df.to_csv(csv_tmp.name, index=False, encoding=ENC)
    csv_tmp.close()
    return df, csv_tmp.name, df_to_tsv(df)

def fsqca_text_wrapper(raw:str):   return _return_outputs(parse_fs_text(raw))
def nca_wrapper(raw:str):          return _return_outputs(parse_nca(raw))
def qca_wrapper(cfg, p, a, n):     return _return_outputs(qca_to_df(cfg, p, a, n))

# --------------------------------------------------
# 构建界面
# --------------------------------------------------
def build_interface():
    with gr.Blocks(title=APP_TITLE) as demo:
        gr.Markdown(f"# {APP_TITLE}")

        # ---------- fsQCA 必要性 ----------
        with gr.Tab(label="fsQCA 必要性整理"):
            gr.Markdown("**粘贴原始 fsQCA 文本（含两个 'inclN RoN covN' 标题块）**")
            raw_txt = gr.Textbox(lines=8, label="原始文本")
            btn_txt = gr.Button("解析 fsQCA")

            out_df  = gr.Dataframe(label="解析结果", interactive=False)
            out_csv = gr.File(label="下载 CSV")
            out_tsv = gr.Textbox(lines=8, label="可复制到 Excel 的结果 (TSV)", interactive=True)

            btn_txt.click(fsqca_text_wrapper, inputs=raw_txt,
                          outputs=[out_df, out_csv, out_tsv])

        # ---------- NCA ----------
        with gr.Tab(label="NCA 结果解析"):
            raw_nca = gr.Textbox(lines=12, label="粘贴 NCA 输出：")
            btn_nca = gr.Button("解析 NCA")
            df_nca  = gr.Dataframe()
            file_nca= gr.File(label="下载 CSV")
            tsv_nca = gr.Textbox(lines=8, label="可复制到 Excel 的结果 (TSV)", interactive=True)
            btn_nca.click(nca_wrapper, inputs=raw_nca,
                          outputs=[df_nca, file_nca, tsv_nca])

        # ---------- QCA 组态 ----------
        with gr.Tab(label="QCA 组态表格"):
            cfg_in = gr.Textbox(lines=5, label="输入组态字符串（多个组态用 + 分隔）：")
            with gr.Row():
                sym_p = gr.Textbox(value="●", label="存在符号", lines=1)
                sym_a = gr.Textbox(value="⊗", label="缺失符号", lines=1)
                sym_n = gr.Textbox(value="",  label="无关符号", lines=1)
            btn_cfg = gr.Button("生成表格")

            df_cfg  = gr.Dataframe()
            file_cfg= gr.File(label="下载 CSV")
            tsv_cfg = gr.Textbox(lines=8, label="可复制到 Excel 的结果 (TSV)", interactive=True)

            btn_cfg.click(qca_wrapper,
                          inputs=[cfg_in, sym_p, sym_a, sym_n],
                          outputs=[df_cfg, file_cfg, tsv_cfg])

    return demo

# --------------------------------------------------
# 入口
# --------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="运行 QCA/NCA Gradio 工具箱 (仅限内网访问)")

    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="监听地址。默认为 127.0.0.1 (仅本机访问)。\n"
             "如需局域网其他设备访问，请设置为 0.0.0.0"
    )

    parser.add_argument(
        "--port",
        type=int,
        default=713,
        help="监听端口 (默认: 713)"
    )
    args = parser.parse_args()

    print(f"应用启动成功！请在浏览器中打开 http://{args.host}:{args.port}")

    build_interface().launch(
        server_name=args.host,
        server_port=args.port,
        share=False,
        inbrowser=True  # 强制在默认浏览器中打开
    )


if __name__ == "__main__":
    main()