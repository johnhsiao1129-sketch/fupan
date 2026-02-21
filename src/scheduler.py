"""
定时任务调度器 - 独立的数据获取服务
使用APScheduler实现定时任务自动采集数据
"""
import logging
import asyncio
from datetime import datetime, time
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from src.data_acquisition import DataAcquisitionService
from src.db_operations import get_last_trading_day, is_trading_day

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('data_acquisition.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)

logger = logging.getLogger(__name__)


class DataAcquisitionScheduler:
    """数据获取调度器"""

    def __init__(self):
        self.scheduler = AsyncIOScheduler(timezone='Asia/Shanghai')
        self.data_service = DataAcquisitionService()
        self.setup_jobs()

    def setup_jobs(self):
        """配置定时任务"""

        # 每日09:25 - 竞价成交额采集
        self.scheduler.add_job(
            self.fetch_auction_amount,
            'cron',
            hour=9,
            minute=25,
            second=0,
            id='auction_amount',
            name='竞价成交额采集',
            replace_existing=True
        )

        # 每日15:00-15:05 - 涨停数据采集（含首板、连板、统计指标）
        self.scheduler.add_job(
            self.fetch_limit_data,
            'cron',
            hour=15,
            minute=5,
            second=0,
            id='limit_data',
            name='涨停数据采集',
            replace_existing=True
        )

        # 每日15:00 - 全天成交额采集
        self.scheduler.add_job(
            self.fetch_full_day_amount,
            'cron',
            hour=15,
            minute=0,
            second=0,
            id='full_day_amount',
            name='全天成交额采集',
            replace_existing=True
        )

        # 每日00:00 - 人气榜初始化采集
        self.scheduler.add_job(
            self.init_popularity_rankings,
            'cron',
            hour=0,
            minute=0,
            second=0,
            id='init_popularity',
            name='人气榜初始化采集',
            replace_existing=True
        )

        # 每15分钟 - 人气榜更新采集
        for minute in [0, 15, 30, 45]:
            self.scheduler.add_job(
                self.update_popularity_rankings,
                'cron',
                minute=minute,
                second=0,
                id=f'popularity_{minute}',
                name=f'人气榜更新({minute}分)',
                replace_existing=True
            )

        logger.info("定时任务配置完成:")
        logger.info("  - 09:25: 竞价成交额采集")
        logger.info("  - 15:00: 全天成交额采集")
        logger.info("  - 15:05: 涨停数据采集（首板、连板、统计指标）")
        logger.info("  - 00:00: 人气榜初始化采集")
        logger.info("  - 每15分钟: 人气榜更新采集")

    async def is_trading_day_now(self) -> bool:
        """判断今天是否是交易日"""
        today = datetime.now().strftime("%Y-%m-%d")
        return is_trading_day(today)

    async def fetch_auction_amount(self):
        """竞价成交额采集任务（09:25）"""
        try:
            logger.info("=" * 60)
            logger.info("开始执行竞价成交额采集任务")

            if not await self.is_trading_day_now():
                logger.info("今天不是交易日，跳过竞价成交额采集")
                return

            result = self.data_service.fetch_and_save_amount_ranking('竞价成交额')

            if result["success"]:
                logger.info(f"✓ 竞价成交额采集成功: {result['record_count']} 条")
            else:
                logger.error(f"✗ 竞价成交额采集失败: {result['message']}")

        except Exception as e:
            logger.error(f"竞价成交额采集任务异常: {e}", exc_info=True)

    async def fetch_limit_data(self):
        """涨停数据采集任务（15:05）"""
        try:
            logger.info("=" * 60)
            logger.info("开始执行涨停数据采集任务")

            if not await self.is_trading_day_now():
                logger.info("今天不是交易日，跳过涨停数据采集")
                return

            # 获取上一个交易日（因为刚收盘）
            trade_date = get_last_trading_day()
            logger.info(f"采集交易日: {trade_date}")

            result = self.data_service.fetch_and_save_limit_data(trade_date)

            if result["success"]:
                logger.info(f"✓ 涨停数据采集成功:")
                logger.info(f"  - 首板数量: {result['first_limit_count']}")
                logger.info(f"  - 连板数量: {result['continuous_limit_count']}")
                logger.info(f"  - 炸板数量: {result['exploded_count']}")
                logger.info(f"  - 跌停数量: {result['limit_down_count']}")
                logger.info(f"  - 总记录数: {result['total_records']}")
            else:
                logger.error(f"✗ 涨停数据采集失败: {result['message']}")

        except Exception as e:
            logger.error(f"涨停数据采集任务异常: {e}", exc_info=True)

    async def fetch_full_day_amount(self):
        """全天成交额采集任务（15:00）"""
        try:
            logger.info("=" * 60)
            logger.info("开始执行全天成交额采集任务")

            if not await self.is_trading_day_now():
                logger.info("今天不是交易日，跳过全天成交额采集")
                return

            result = self.data_service.fetch_and_save_amount_ranking('全天成交额')

            if result["success"]:
                logger.info(f"✓ 全天成交额采集成功: {result['record_count']} 条")
            else:
                logger.error(f"✗ 全天成交额采集失败: {result['message']}")

        except Exception as e:
            logger.error(f"全天成交额采集任务异常: {e}", exc_info=True)

    async def init_popularity_rankings(self):
        """人气榜初始化采集任务（00:00）"""
        try:
            logger.info("=" * 60)
            logger.info("开始执行人气榜初始化采集任务")

            sources = ['热门关注', '资金流向', '热门交易']

            for source in sources:
                result = self.data_service.fetch_and_save_popularity_ranking(source)

                if result["success"]:
                    logger.info(f"✓ {source}采集成功: {result['record_count']} 条")
                else:
                    logger.warning(f"✗ {source}采集失败: {result['message']}")

        except Exception as e:
            logger.error(f"人气榜初始化采集任务异常: {e}", exc_info=True)

    async def update_popularity_rankings(self):
        """人气榜更新采集任务（每15分钟）"""
        try:
            # 检查是否在交易时间内
            now = datetime.now().time()
            trading_time_start = time(9, 15)
            trading_time_end = time(15, 0)

            if now < trading_time_start or now > trading_time_end:
                logger.debug("不在交易时间内，跳过人气榜更新")
                return

            if not await self.is_trading_day_now():
                logger.debug("今天不是交易日，跳过人气榜更新")
                return

            logger.debug("开始执行人气榜更新采集任务")

            sources = ['热门关注', '资金流向', '热门交易']

            for source in sources:
                result = self.data_service.fetch_and_save_popularity_ranking(source)

                if result["success"]:
                    logger.debug(f"✓ {source}更新成功: {result['record_count']} 条")
                else:
                    logger.debug(f"✗ {source}更新失败: {result['message']}")

        except Exception as e:
            logger.debug(f"人气榜更新采集任务异常: {e}")

    async def run_manual_task(self, task_name: str, **kwargs):
        """手动执行单个任务（用于测试）"""
        logger.info(f"手动执行任务: {task_name}")

        if task_name == 'limit_data':
            await self.fetch_limit_data()
        elif task_name == 'auction_amount':
            await self.fetch_auction_amount()
        elif task_name == 'full_day_amount':
            await self.fetch_full_day_amount()
        elif task_name == 'init_popularity':
            await self.init_popularity_rankings()
        elif task_name == 'update_popularity':
            await self.update_popularity_rankings()
        else:
            logger.error(f"未知的任务名称: {task_name}")

    def start(self):
        """启动调度器"""
        logger.info("=" * 60)
        logger.info("数据获取服务启动中...")
        logger.info("=" * 60)

        self.scheduler.start()
        logger.info("✓ 数据获取服务已启动")
        logger.info("定时任务列表:")

        for job in self.scheduler.get_jobs():
            logger.info(f"  - {job.name}: {job.next_run_time}")

    def shutdown(self):
        """关闭调度器"""
        logger.info("数据获取服务关闭中...")
        self.scheduler.shutdown()
        logger.info("✓ 数据获取服务已关闭")


async def main():
    """主函数"""
    scheduler = DataAcquisitionScheduler()

    try:
        scheduler.start()

        # 保持运行
        logger.info("\n数据获取服务正在运行，按 Ctrl+C 停止...\n")

        # 模拟运行（实际生产环境会一直运行）
        while True:
            await asyncio.sleep(3600)  # 每小时检查一次

    except KeyboardInterrupt:
        logger.info("\n收到停止信号")
        scheduler.shutdown()
    except Exception as e:
        logger.error(f"服务异常: {e}", exc_info=True)
        scheduler.shutdown()


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == 'test':
        # 测试模式：手动执行所有任务
        import asyncio as aio

        scheduler = DataAcquisitionScheduler()

        async def test_all():
            logger.info("=" * 60)
            logger.info("测试模式：手动执行所有任务")
            logger.info("=" * 60)

            await scheduler.fetch_limit_data()
            await scheduler.fetch_auction_amount()
            await scheduler.fetch_full_day_amount()
            await scheduler.init_popularity_rankings()

            logger.info("=" * 60)
            logger.info("测试完成")
            logger.info("=" * 60)

        aio.run(test_all())

    else:
        # 正常模式：启动调度器
        asyncio.run(main())
