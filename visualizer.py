# visualizer.py
"""可视化模块 — 生成指数趋势图"""

import pandas as pd
import matplotlib.pyplot as plt
import logging

logger = logging.getLogger(__name__)

from db import get_top_categories

# 设置中文字体
plt.rcParams['font.sans-serif'] = ['SimHei', 'Arial Unicode MS', 'DejaVu Sans']
plt.rcParams['axes.unicode_minus'] = False


class Visualizer:
    """可视化工具（不再持有 client，仅操作 DataFrame）"""

    def plot_price_index(self, index_df, title="高频电商价格指数趋势图", save_path=None, show=True):
        """
        生成价格指数趋势图
        """
        logger.info("开始生成价格指数趋势图...")

        if index_df.empty:
            logger.warning("无数据可展示")
            return

        fig, axes = plt.subplots(2, 2, figsize=(16, 12))
        fig.suptitle(title, fontsize=16, fontweight='bold')

        # 1. 全局指数趋势
        self._plot_global_trend(axes[0, 0], index_df)

        # 2. 分类指数趋势（Top 5）
        self._plot_category_trends(axes[0, 1], index_df)

        # 3. 日环比变化
        self._plot_daily_change(axes[1, 0], index_df)

        # 4. 热力图
        self._plot_heatmap(axes[1, 1], index_df)

        plt.tight_layout()

        if save_path:
            plt.savefig(save_path, dpi=300, bbox_inches='tight')
            logger.info(f"图表已保存: {save_path}")

        if show:
            plt.show()
        else:
            plt.close(fig)
        logger.info("图表生成完成")

    def _plot_global_trend(self, ax, index_df):
        """全局指数趋势"""
        if 'global_index' in index_df.columns:
            ax.plot(index_df['date'], index_df['global_index'],
                    linewidth=2, color='darkblue', label='全局指数')
        else:
            pivot_df = index_df.pivot_table(
                index='date', columns='category_name', values='index_value'
            )
            avg_index = pivot_df.mean(axis=1)
            ax.plot(avg_index.index, avg_index, linewidth=2, color='darkblue', label='平均指数')

        ax.set_title('全局价格指数趋势', fontsize=12)
        ax.set_xlabel('日期')
        ax.set_ylabel('指数 (基期=100)')
        ax.legend()
        ax.grid(True, alpha=0.3)
        ax.axhline(y=100, color='red', linestyle='--', linewidth=0.8, alpha=0.5)

    def _plot_category_trends(self, ax, index_df):
        """分类指数趋势"""
        top_categories = get_top_categories(5)

        pivot_df = index_df.pivot_table(
            index='date', columns='category_name', values='index_value'
        )

        colors = ['#1f77b4', '#ff7f0e', '#2ca02c', '#d62728', '#9467bd']
        for i, cat in enumerate(top_categories):
            if cat in pivot_df.columns:
                pivot_df[cat].plot(ax=ax, linewidth=1.5, label=cat, color=colors[i % len(colors)])

        ax.set_title('主要分类价格指数趋势', fontsize=12)
        ax.set_xlabel('日期')
        ax.set_ylabel('指数 (基期=100)')
        ax.legend(loc='upper left', fontsize=8)
        ax.grid(True, alpha=0.3)
        ax.axhline(y=100, color='red', linestyle='--', linewidth=0.8, alpha=0.5)

    def _plot_daily_change(self, ax, index_df):
        """日环比变化"""
        if 'global_index' in index_df.columns:
            daily_change = index_df['global_index'].pct_change() * 100
            ax.bar(index_df['date'][1:], daily_change[1:], alpha=0.6, color='steelblue')
        else:
            pivot_df = index_df.pivot_table(
                index='date', columns='category_name', values='index_value'
            )
            avg_index = pivot_df.mean(axis=1)
            daily_change = avg_index.pct_change() * 100
            ax.bar(avg_index.index[1:], daily_change[1:], alpha=0.6, color='steelblue')

        ax.axhline(y=0, color='red', linestyle='--', linewidth=0.8)
        ax.set_title('日环比变化率', fontsize=12)
        ax.set_xlabel('日期')
        ax.set_ylabel('变化率 (%)')
        ax.grid(True, alpha=0.3)

    def _plot_heatmap(self, ax, index_df):
        """分类指数热力图"""
        if len(index_df) == 0:
            return

        unique_dates = sorted(index_df['date'].unique())
        recent_dates = unique_dates[-30:] if len(unique_dates) > 30 else unique_dates

        recent_df = index_df[index_df['date'].isin(recent_dates)]
        cat_counts = recent_df.groupby('category_name')['date'].nunique()
        top_cats = cat_counts[cat_counts >= len(recent_dates) * 0.5].head(10).index.tolist()

        if top_cats:
            recent_df = recent_df[recent_df['category_name'].isin(top_cats)]
            pivot_heat = recent_df.pivot_table(
                index='date', columns='category_name', values='index_value'
            )

            if len(pivot_heat) > 0 and len(pivot_heat.columns) > 0:
                pivot_heat = pivot_heat / pivot_heat.iloc[0] * 100

                im = ax.imshow(pivot_heat.T, aspect='auto', cmap='RdYlGn_r')
                ax.set_title('各分类指数相对表现 (近30天)', fontsize=12)
                ax.set_xlabel('日期')
                ax.set_ylabel('分类')

                step = max(1, len(pivot_heat.index) // 8)
                ax.set_xticks(range(0, len(pivot_heat.index), step))
                ax.set_xticklabels(
                    [d.strftime('%m-%d') for d in pivot_heat.index[::step]], fontsize=8
                )
                ax.set_yticks(range(len(pivot_heat.columns)))
                ax.set_yticklabels(pivot_heat.columns, fontsize=7)
                plt.colorbar(im, ax=ax, label='指数值')
