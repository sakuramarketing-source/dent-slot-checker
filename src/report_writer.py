"""スクレイピングレポート生成モジュール"""

import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Any, Optional


class ClinicReport:
    """分院ごとのスクレイピングレポート"""

    def __init__(self, name: str, system: str):
        self.name = name
        self.system = system
        self.status = "pending"
        self.started_at: Optional[datetime] = None
        self.completed_at: Optional[datetime] = None
        self.staff_found: List[Dict] = []
        self.staff_skipped: List[Dict] = []
        self.staff_zero_slots: List[str] = []
        self.total_staff_in_system: int = 0
        self.error: Optional[str] = None
        self.warning_message: Optional[str] = None

    def start(self):
        """処理開始を記録"""
        self.started_at = datetime.now()
        self.status = "running"

    def complete(self, status: str = "success"):
        """処理完了を記録"""
        self.completed_at = datetime.now()
        self.status = status

    def add_staff_found(self, name: str, slots: int):
        """検出されたスタッフを追加"""
        self.staff_found.append({
            'name': name,
            'slots': slots,
            'status': 'found'
        })

    def add_staff_skipped(self, name: str, reason: str, pattern: str = None):
        """スキップされたスタッフを追加"""
        entry = {
            'name': name,
            'reason': reason
        }
        if pattern:
            entry['pattern'] = pattern
        self.staff_skipped.append(entry)

    def add_staff_zero_slots(self, name: str):
        """スロット0のスタッフを追加"""
        if name not in self.staff_zero_slots:
            self.staff_zero_slots.append(name)

    def set_error(self, error: str):
        """エラーを設定"""
        self.error = error
        self.status = "error"

    def set_warning(self, message: str):
        """警告を設定"""
        self.warning_message = message
        if self.status != "error":
            self.status = "warning"

    def to_dict(self) -> Dict:
        """辞書に変換"""
        duration = None
        if self.started_at and self.completed_at:
            duration = (self.completed_at - self.started_at).total_seconds()

        return {
            "name": self.name,
            "system": self.system,
            "status": self.status,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "duration_seconds": duration,
            "staff_found": self.staff_found,
            "staff_skipped": self.staff_skipped,
            "staff_zero_slots": self.staff_zero_slots,
            "total_staff_in_system": self.total_staff_in_system,
            "total_staff_processed": len(self.staff_found),
            "error": self.error,
            "warning_message": self.warning_message
        }


class ScrapingReport:
    """スクレイピング実行全体のレポート"""

    def __init__(self, config: Dict = None):
        self.run_id = datetime.now().strftime('%Y%m%d_%H%M%S')
        self.started_at = datetime.now()
        self.completed_at: Optional[datetime] = None
        self.status = "running"
        self.config = config or {}
        self.clinics: List[ClinicReport] = []
        self.errors: List[str] = []

    def create_clinic_report(self, name: str, system: str) -> ClinicReport:
        """分院レポートを作成して追加"""
        report = ClinicReport(name, system)
        self.clinics.append(report)
        return report

    def add_error(self, error: str):
        """グローバルエラーを追加"""
        self.errors.append(error)

    def complete(self, status: str = "completed"):
        """レポートを完了"""
        self.completed_at = datetime.now()
        self.status = status

    def to_dict(self) -> Dict:
        """辞書に変換"""
        # サマリーを計算
        successful = sum(1 for c in self.clinics if c.status == "success")
        warnings = sum(1 for c in self.clinics if c.status == "warning")
        errors = sum(1 for c in self.clinics if c.status == "error")

        duration = None
        if self.started_at and self.completed_at:
            duration = (self.completed_at - self.started_at).total_seconds()

        check_date = (datetime.now() + timedelta(days=1)).strftime('%Y-%m-%d')

        return {
            "run_id": self.run_id,
            "started_at": self.started_at.isoformat(),
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "duration_seconds": duration,
            "status": self.status,
            "check_date": check_date,
            "config": self.config,
            "clinics": [c.to_dict() for c in self.clinics],
            "summary": {
                "total_clinics": len(self.clinics),
                "successful_clinics": successful,
                "warning_clinics": warnings,
                "error_clinics": errors,
                "total_staff_processed": sum(len(c.staff_found) for c in self.clinics),
                "total_staff_skipped": sum(len(c.staff_skipped) for c in self.clinics),
                "total_staff_zero_slots": sum(len(c.staff_zero_slots) for c in self.clinics)
            },
            "errors": self.errors
        }

    def save(self, output_dir: Path) -> str:
        """レポートをJSONファイルに保存"""
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        filename = f"scrape_report_{self.run_id}.json"
        filepath = output_dir / filename

        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(self.to_dict(), f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())

        return str(filepath)
