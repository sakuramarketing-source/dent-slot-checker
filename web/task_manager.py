"""バックグラウンドタスク管理"""

import json
import os
import threading
import time
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional, Any
from dataclasses import dataclass, asdict

from src.gcs_task_storage import (
    save_task_to_gcs,
    load_task_from_gcs,
    is_gcs_enabled
)

logger = logging.getLogger(__name__)


@dataclass
class TaskProgress:
    """タスク進捗情報"""
    current: int = 0
    total: int = 0
    current_clinic: str = ""


@dataclass
class TaskInfo:
    """タスク情報"""
    task_id: str
    status: str  # pending, running, completed, failed
    started_at: str
    updated_at: str
    completed_at: Optional[str] = None
    progress: Optional[TaskProgress] = None
    error: Optional[str] = None
    result: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict:
        """辞書に変換（JSON化用）"""
        data = asdict(self)
        if self.progress:
            data['progress'] = asdict(self.progress)
        return data


class TaskManager:
    """タスク管理クラス（シングルトン）"""

    _instance = None
    _lock = threading.Lock()

    def __new__(cls, output_dir: Path = None):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self, output_dir: Path = None):
        if self._initialized:
            return

        self.output_dir = output_dir or Path('output')
        self.tasks_dir = self.output_dir / 'tasks'

        # ディレクトリ作成の検証
        try:
            self.tasks_dir.mkdir(parents=True, exist_ok=True)
            if not self.tasks_dir.exists():
                raise OSError(f"Failed to create tasks directory: {self.tasks_dir}")
        except Exception as e:
            import logging
            logging.error(f"TaskManager initialization error: {e}")
            raise

        # メモリ内タスクキャッシュ（起動中のみ有効）
        self._tasks: Dict[str, TaskInfo] = {}
        self._tasks_lock = threading.Lock()

        self._initialized = True

    def create_task(self) -> str:
        """新しいタスクを作成してIDを返す"""
        task_id = datetime.now().strftime('%Y%m%d_%H%M%S')

        task_info = TaskInfo(
            task_id=task_id,
            status='pending',
            started_at=datetime.now().isoformat(),
            updated_at=datetime.now().isoformat(),
            progress=TaskProgress(current=0, total=0)
        )

        with self._tasks_lock:
            self._tasks[task_id] = task_info
            self._save_task_to_file(task_info)

        return task_id

    def get_task(self, task_id: str) -> Optional[TaskInfo]:
        """タスク情報を取得"""
        with self._tasks_lock:
            # メモリキャッシュを優先
            if task_id in self._tasks:
                return self._tasks[task_id]

            # ファイルから読み込み
            return self._load_task_from_file(task_id)

    def update_task(self, task_id: str, **kwargs):
        """タスク情報を更新"""
        with self._tasks_lock:
            task = self._tasks.get(task_id) or self._load_task_from_file(task_id)
            if not task:
                return

            for key, value in kwargs.items():
                if hasattr(task, key):
                    setattr(task, key, value)

            task.updated_at = datetime.now().isoformat()
            self._tasks[task_id] = task
            self._save_task_to_file(task)

    def update_progress(self, task_id: str, current: int, total: int, clinic_name: str = ""):
        """進捗を更新"""
        with self._tasks_lock:
            task = self._tasks.get(task_id)
            if task:
                task.progress = TaskProgress(
                    current=current,
                    total=total,
                    current_clinic=clinic_name
                )
                task.updated_at = datetime.now().isoformat()
                self._save_task_to_file(task)

    def complete_task(self, task_id: str, result: Dict[str, Any]):
        """タスクを完了としてマーク"""
        self.update_task(
            task_id,
            status='completed',
            completed_at=datetime.now().isoformat(),
            result=result
        )

    def fail_task(self, task_id: str, error: str):
        """タスクを失敗としてマーク"""
        self.update_task(
            task_id,
            status='failed',
            completed_at=datetime.now().isoformat(),
            error=error
        )

    def _save_task_to_file(self, task: TaskInfo):
        """タスクをファイルとGCSに保存"""
        task_dict = task.to_dict()

        # 1. GCSに保存（Cloud Run用）
        if is_gcs_enabled():
            if not save_task_to_gcs(task.task_id, task_dict):
                logger.warning(f"GCS保存失敗: task_{task.task_id}")

        # 2. ローカルファイルに保存（ローカル開発用）
        task_file = self.tasks_dir / f'task_{task.task_id}.json'
        try:
            with open(task_file, 'w', encoding='utf-8') as f:
                json.dump(task_dict, f, ensure_ascii=False, indent=2)
                f.flush()  # Pythonバッファをフラッシュ
                os.fsync(f.fileno())  # ディスクへ強制同期
        except Exception as e:
            logger.error(f"ローカルファイル保存失敗 {task_file}: {e}")
            # タスクはメモリに残っているので、ファイル保存失敗でも継続

    def _load_task_from_file(self, task_id: str) -> Optional[TaskInfo]:
        """ファイルまたはGCSからタスクを読み込み"""

        # 1. GCSから読み込み（Cloud Run用）
        if is_gcs_enabled():
            task_data = load_task_from_gcs(task_id)
            if task_data:
                # progressを復元
                if task_data.get('progress'):
                    task_data['progress'] = TaskProgress(**task_data['progress'])
                return TaskInfo(**task_data)

        # 2. ローカルファイルから読み込み（ローカル開発用）
        task_file = self.tasks_dir / f'task_{task_id}.json'
        if not task_file.exists():
            return None

        try:
            with open(task_file, 'r', encoding='utf-8') as f:
                task_data = json.load(f)

            # progressを復元
            if task_data.get('progress'):
                task_data['progress'] = TaskProgress(**task_data['progress'])

            return TaskInfo(**task_data)
        except Exception as e:
            logger.error(f"ローカルファイル読み込みエラー: {e}")
            return None

    def cleanup_old_tasks(self, max_age_hours: int = 24):
        """古いタスクファイルを削除"""
        cutoff_time = time.time() - (max_age_hours * 3600)

        for task_file in self.tasks_dir.glob('task_*.json'):
            if task_file.stat().st_mtime < cutoff_time:
                task_file.unlink()
