"""知识库管理 - 对话框组件"""
from __future__ import annotations

from typing import Optional, List

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QLabel, QLineEdit, QTextEdit,
    QDialogButtonBox, QCheckBox, QFrame, QHBoxLayout,
)

from database.models import ProductKnowledge, CustomerServiceKnowledge


class ProductDetailDialog(QDialog):
    """产品知识详情对话框，支持编辑"""

    def __init__(self, product: ProductKnowledge, parent=None):
        super().__init__(parent)
        self.product = product
        self.setWindowTitle("产品知识详情")
        self.resize(700, 500)
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)

        # 商品名称
        self.name_label = QLabel("商品名称:")
        self.name_edit = QLineEdit(self.product.goods_name)
        layout.addWidget(self.name_label)
        layout.addWidget(self.name_edit)

        # 提取内容
        self.content_label = QLabel("提取知识:")
        self.content_edit = QTextEdit()
        self.content_edit.setPlainText(self.product.extracted_content or "")
        self.content_edit.setPlaceholderText("LLM提取的产品知识会显示在这里，你可以手动编辑")
        layout.addWidget(self.content_label)
        layout.addWidget(self.content_edit)

        # 按钮
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def get_data(self):
        """获取编辑后的数据"""
        return {
            "goods_name": self.name_edit.text().strip(),
            "extracted_content": self.content_edit.toPlainText().strip(),
        }


class CsAddEditDialog(QDialog):
    """客服知识添加/编辑对话框"""

    def __init__(
        self,
        shop_id: int,
        existing: Optional[CustomerServiceKnowledge] = None,
        parent=None,
    ):
        super().__init__(parent)
        self.shop_id = shop_id
        self.existing = existing
        self.default_tags = ["物流", "售后", "支付", "商品规格", "优惠券", "会员", "发货时间", "退换货"]
        self.setWindowTitle("添加客服知识" if not existing else "编辑客服知识")
        self.resize(650, 500)
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)

        # 标题
        self.title_label = QLabel("标题:")
        self.title_edit = QLineEdit()
        if self.existing:
            self.title_edit.setText(self.existing.title)
        layout.addWidget(self.title_label)
        layout.addWidget(self.title_edit)

        # 内容
        self.content_label = QLabel("内容:")
        self.content_edit = QTextEdit()
        self.content_edit.setPlaceholderText("输入客服知识内容，例如：退换货政策说明...")
        if self.existing:
            self.content_edit.setText(self.existing.content)
        layout.addWidget(self.content_label)
        layout.addWidget(self.content_edit)

        # 标签 - 预设复选框
        self.tags_label = QLabel("选择标签 (可多选):")
        layout.addWidget(self.tags_label)

        self.tag_checkboxes: List[QCheckBox] = []
        existing_tags = []
        if self.existing and self.existing.tags:
            existing_tags = [t.strip() for t in self.existing.tags.split(',') if t.strip()]

        tags_frame = QFrame()
        tags_layout = QHBoxLayout(tags_frame)
        tags_layout.setSpacing(8)

        for tag in self.default_tags:
            cb = QCheckBox(tag)
            if tag in existing_tags:
                cb.setChecked(True)
            tags_layout.addWidget(cb)
            self.tag_checkboxes.append(cb)

        layout.addWidget(tags_frame)

        # 自定义标签
        self.custom_label = QLabel("自定义标签 (逗号分隔):")
        self.custom_edit = QLineEdit()
        if self.existing and self.existing.tags:
            # 已有标签中不在预设列表的合并到自定义
            existing_custom = [
                t for t in existing_tags
                if t not in self.default_tags
            ]
            if existing_custom:
                self.custom_edit.setText(','.join(existing_custom))
        layout.addWidget(self.custom_label)
        layout.addWidget(self.custom_edit)

        # 启用
        self.enabled_cb = QCheckBox("启用")
        self.enabled_cb.setChecked(True)
        if self.existing:
            self.enabled_cb.setChecked(self.existing.enabled)
        layout.addWidget(self.enabled_cb)

        # 按钮
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def get_data(self):
        """获取数据"""
        title = self.title_edit.text().strip()
        content = self.content_edit.toPlainText().strip()
        enabled = self.enabled_cb.isChecked()

        # 收集选中的预设标签
        selected_tags = [
            cb.text() for cb in self.tag_checkboxes
                if cb.isChecked()
        ]

        # 添加自定义标签
        custom = self.custom_edit.text().strip()
        if custom:
            selected_tags.extend([t.strip() for t in custom.split(',') if t.strip()])

        # 去重
        selected_tags = list(dict.fromkeys(selected_tags))
        tags_str = ','.join(selected_tags) if selected_tags else None

        return {
            "title": title,
            "content": content,
            "tags": tags_str,
            "enabled": enabled,
        }
