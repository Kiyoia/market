# index_calculator.py
"""指数计算模块 — 链式价格指数（费雪理想指数）"""

import pandas as pd
import numpy as np
import logging

logger = logging.getLogger(__name__)

from db import get_categories


class IndexCalculator:
    """指数计算器"""

    def __init__(self, base_date):
        self.base_date = pd.to_datetime(base_date)

    def compute_weighted_avg_price(self, cleaned_df):
        """
        计算分类销量加权平均价格
        """
        logger.info("开始计算分类加权平均价格...")

        if cleaned_df.empty:
            return pd.DataFrame()

        df = cleaned_df.copy()
        df['category_id'] = df['category_id'].astype(str).str.strip()

        # 如果没有销量，使用权重替代
        if 'sales_qty' not in df.columns:
            df['sales_qty'] = df['weight'] * 1000
            df['sales_amount'] = df['price'] * df['sales_qty']

        df['weighted_price'] = df['price'] * df['sales_qty']

        # 按日期和分类分组
        category_daily = df.groupby(['date', 'category_id']).agg({
            'weighted_price': 'sum',
            'sales_qty': 'sum',
            'price': 'mean'
        }).reset_index()

        category_daily['weighted_avg_price'] = (
            category_daily['weighted_price'] / category_daily['sales_qty'].clip(lower=0.001)
        )

        # 获取分类信息，并直接归并到二级分类
        categories = get_categories()
        categories['category_id'] = categories['category_id'].astype(str).str.strip()
        categories['parent'] = categories['parent'].astype(str).str.strip()
        category_daily = category_daily.merge(
            categories[['category_id', 'category_name', 'hierarchy', 'parent']],
            on='category_id',
            how='left'
        )
        category_daily['category_id'] = category_daily.apply(
            lambda row: row['parent'] if row['hierarchy'] == 3 else row['category_id'],
            axis=1
        )
        category_daily = category_daily.groupby(['date', 'category_id']).agg({
            'weighted_price': 'sum',
            'sales_qty': 'sum',
            'price': 'mean'
        }).reset_index()
        category_daily['weighted_avg_price'] = (
            category_daily['weighted_price'] / category_daily['sales_qty'].clip(lower=0.001)
        )
        category_daily = category_daily.merge(
            categories[['category_id', 'category_name']],
            on='category_id',
            how='left'
        )
        category_daily['category_name'] = category_daily['category_name'].fillna(
            category_daily['category_id']
        )
        category_daily = category_daily.sort_values(['date', 'category_id'])

        logger.info(f"计算完成: {len(category_daily)} 条")
        return category_daily

    def compute_chain_price_index(self, category_daily, base_date=None):
        """
        链式价格指数计算（费雪理想指数）
        """
        logger.info("开始计算链式价格指数...")

        if category_daily.empty:
            return pd.DataFrame()

        if base_date is None:
            base_date = self.base_date

        category_daily['date'] = pd.to_datetime(category_daily['date'])
        base_date = pd.to_datetime(base_date)

        all_dates = sorted(category_daily['date'].unique())
        all_categories = category_daily['category_id'].unique()

        if not all_dates:
            return pd.DataFrame()

        if base_date not in all_dates:
            base_date = all_dates[0]
            logger.info(f"基期调整为: {base_date}")

        base_idx = all_dates.index(base_date)
        results = []

        for category_id in all_categories:
            cat_data = category_daily[category_daily['category_id'] == category_id].copy()
            cat_data = cat_data.sort_values('date')
            cat_name = cat_data['category_name'].iloc[0] if not cat_data.empty else category_id

            # 获取基期数据
            base_data = cat_data[cat_data['date'] == base_date]
            if base_data.empty:
                base_data = cat_data.iloc[[0]]
                logger.debug(f"分类 {category_id} 使用最早日期作为基期")

            p0 = base_data['weighted_avg_price'].iloc[0]
            q0 = max(base_data['sales_qty'].sum(), 1)

            # 逐日计算
            prev_index = 100.0
            prev_price = p0

            for i, date in enumerate(all_dates):
                if i < base_idx:
                    continue

                current_data = cat_data[cat_data['date'] == date]
                if current_data.empty:
                    continue

                pt = current_data['weighted_avg_price'].iloc[0]
                qt = max(current_data['sales_qty'].sum(), 1)

                # 费雪理想指数
                if p0 > 0 and q0 > 0 and prev_price > 0:
                    laspeyres = (pt * q0) / (p0 * q0)
                    paasche = (pt * qt) / (p0 * qt)
                    fisher = np.sqrt(max(laspeyres * paasche, 0))
                else:
                    laspeyres = paasche = fisher = 1

                # 链式连接
                if i == base_idx:
                    index_value = 100.0
                else:
                    index_value = prev_index * (pt / prev_price) if prev_price > 0 else prev_index

                results.append({
                    'date': date,
                    'category_id': category_id,
                    'category_name': cat_name,
                    'index_value': index_value,
                    'weighted_price': pt,
                    'fisher': fisher * 100
                })

                prev_index = index_value
                prev_price = pt

        result_df = pd.DataFrame(results)
        logger.info(f"指数计算完成: {len(result_df)} 条")
        return result_df

    def compute_aggregated_index(self, index_df, level='global'):
        """
        计算汇总指数
        """
        logger.info(f"计算 {level} 层级汇总指数...")

        if index_df.empty:
            return pd.DataFrame()

        if level == 'global':
            # 获取分类权重（仅 hierarchy=2）
            categories = get_categories(hierarchy=2)
            categories['category_id'] = categories['category_id'].astype(str).str.strip()

            global_index = index_df.merge(
                categories[['category_id', 'weight']], on='category_id', how='left'
            )
            missing_count = int(global_index['weight'].isna().sum())
            if missing_count:
                logger.warning(f"跳过缺少二级分类权重的指数记录: {missing_count} 条")
            global_index = global_index[global_index['weight'].notna()].copy()
            if global_index.empty:
                logger.warning("无可汇总的二级分类权重数据")
                return pd.DataFrame()

            global_index['weighted_index'] = (
                global_index['index_value'] * global_index['weight']
            )

            result = global_index.groupby('date').agg({
                'weighted_index': 'sum',
                'weight': 'sum'
            }).reset_index()

            result = result[result['weight'] > 0].copy()
            result['global_index'] = result['weighted_index'] / result['weight']
            result = result[['date', 'global_index']]

        elif level == 'parent':
            categories = get_categories(hierarchy=2)
            categories['category_id'] = categories['category_id'].astype(str).str.strip()
            parent_index = index_df.merge(
                categories[['category_id', 'parent']], on='category_id', how='left'
            )
            result = parent_index.groupby(['date', 'parent']).agg({
                'index_value': 'mean'
            }).reset_index()
            result = result.rename(columns={'index_value': 'parent_index'})
        else:
            result = index_df[['date', 'category_id', 'category_name', 'index_value']]

        logger.info("汇总完成")
        return result
