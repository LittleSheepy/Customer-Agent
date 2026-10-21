"""知识库管理 - 后台工作线程"""
from __future__ import annotations

import asyncio
from typing import List

from PyQt6.QtCore import pyqtSignal, QThread

from database.product_sync import ProductSyncService, SyncProgress


class SyncWorker(QThread):
    """同步工作线程"""
    progress_updated = pyqtSignal(int, int, int, str, str)  # current, total, success, current_name, phase
    sync_finished = pyqtSignal(int, int, bool)  # success, failed, cancelled

    def __init__(
        self,
        shop_db_id: int,
        pdd_shop_id: str,
        user_id: str,
        is_full_sync: bool,
        product_sync: ProductSyncService,
        parent=None,
    ):
        super().__init__(parent)
        self.shop_db_id = shop_db_id
        self.pdd_shop_id = pdd_shop_id
        self.user_id = user_id
        self.is_full_sync = is_full_sync
        self.product_sync = product_sync
        self._cancelled = False

    def run(self):
        """运行同步"""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        def progress_callback(progress: SyncProgress):
            self.progress_updated.emit(
                progress.current,
                progress.total,
                progress.success,
                progress.current_goods_name,
                progress.phase,
            )

        result = loop.run_until_complete(
            self.product_sync.sync_shop(
                shop_id=int(self.pdd_shop_id),
                shop_db_id=self.shop_db_id,
                user_id=self.user_id,
                is_full_sync=self.is_full_sync,
                progress_callback=progress_callback,
            )
        )

        loop.close()
        self.sync_finished.emit(result.success, result.failed, result.cancelled)


class DocumentAddWorker(QThread):
    """文档添加/向量化后台线程（Embedding 是网络调用，避免阻塞 UI）"""
    progress_updated = pyqtSignal(int, int, str)   # 已完成, 总数, 当前文件名
    add_finished = pyqtSignal(list)                # [(成功bool, 文件名, 说明)]

    def __init__(self, doc_service, shop_db_id: int, file_paths: List[str], parent=None):
        super().__init__(parent)
        self.doc_service = doc_service
        self.shop_db_id = shop_db_id
        self.file_paths = file_paths

    def run(self):
        """执行批量添加，进度通过信号回传主线程"""
        results = self.doc_service.add_files(
            self.shop_db_id,
            self.file_paths,
            progress_cb=lambda done, total, name:
                self.progress_updated.emit(done, total, name),
        )
        self.add_finished.emit(results)
