"""SQLAlchemy models for channel, account and knowledge data."""

from datetime import datetime

from sqlalchemy import (
    Boolean, Column, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint,
)
from sqlalchemy.orm import declarative_base, relationship


Base = declarative_base()


class Account(Base):
    __tablename__ = "accounts"
    __table_args__ = (
        UniqueConstraint("shop_db_id", "user_id", name="uix_account_shop_user"),
        UniqueConstraint("shop_db_id", "username", name="uix_account_shop_username"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    shop_db_id = Column(Integer, ForeignKey("shops.id"), nullable=False)
    user_id = Column(String(100), nullable=False)
    username = Column(String(100), nullable=False)
    # Stored as a DPAPI-protected value on Windows; legacy plaintext rows are
    # still readable for backwards compatibility.
    password = Column(String(255), nullable=False)
    cookies = Column(Text)
    status = Column(Integer, default=None)
    shop = relationship("Shop", back_populates="accounts")

    def __repr__(self):
        return f"<Account(username='{self.username}')>"


class AgentMessage(Base):
    """Agent 会话消息模型"""
    __tablename__ = "agent_messages"

    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(String(255), nullable=False, index=True)
    role = Column(String(32), nullable=False)  # user | assistant | tool | summary
    content = Column(Text, nullable=True)
    tool_call_id = Column(String(128), nullable=True)  # 关联 LLM 请求中的 tool_call id
    timestamp = Column(DateTime, default=datetime.now, nullable=False)

    __table_args__ = (
        Index("ix_agent_messages_session_timestamp", "session_id", "timestamp"),
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "session_id": self.session_id,
            "role": self.role,
            "content": self.content,
            "tool_call_id": self.tool_call_id,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
        }


class Channel(Base):
    __tablename__ = "channels"

    id = Column(Integer, primary_key=True, autoincrement=True)
    channel_name = Column(String(50), unique=True, nullable=False)
    description = Column(String(255))
    shops = relationship("Shop", back_populates="channel", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<Channel(channel_name='{self.channel_name}')>"


class CustomerServiceKnowledge(Base):
    __tablename__ = "customer_service_knowledge"

    id = Column(Integer, primary_key=True, autoincrement=True)
    shop_db_id = Column(Integer, ForeignKey("shops.id", ondelete="CASCADE"), nullable=False)
    title = Column(String(255), nullable=False)
    content = Column(Text, nullable=False)
    tags = Column(String(255), nullable=True)
    enabled = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)
    shop = relationship("Shop", backref="customer_service_knowledge")

    def __repr__(self):
        return f"<CustomerServiceKnowledge(title='{self.title}', enabled={self.enabled})>"


class Keyword(Base):
    __tablename__ = "keywords"
    __table_args__ = (UniqueConstraint("keyword", name="uix_keyword_keyword"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    keyword = Column(String(100), nullable=False)

    def __repr__(self):
        return f"<Keyword(keyword='{self.keyword}')>"


class KnowledgeDocument(Base):
    """上传的知识文档（PDF/DOCX/TXT/MD），分块后存入独立向量集合"""
    __tablename__ = "knowledge_documents"
    __table_args__ = (
        UniqueConstraint("shop_db_id", "file_hash", name="uix_knowledge_document_shop_hash"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    shop_db_id = Column(Integer, ForeignKey("shops.id", ondelete="CASCADE"), nullable=False)
    file_name = Column(String(255), nullable=False)        # 原始文件名
    stored_path = Column(String(500), nullable=False)      # 项目内保存路径
    file_hash = Column(String(64), nullable=False)         # 文件 MD5，同店铺去重
    chunk_count = Column(Integer, default=0, nullable=False)  # 向量化分块数
    enabled = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=datetime.now)
    shop = relationship("Shop", backref="knowledge_documents")

    def __repr__(self):
        return f"<KnowledgeDocument(file_name='{self.file_name}', enabled={self.enabled})>"


class ProductKnowledge(Base):
    __tablename__ = "product_knowledge"
    __table_args__ = (
        UniqueConstraint("shop_db_id", "goods_id", name="uix_product_knowledge_shop_goods"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    shop_db_id = Column(Integer, ForeignKey("shops.id", ondelete="CASCADE"), nullable=False)
    goods_id = Column(Integer, nullable=False)
    goods_name = Column(String(255), nullable=False)
    price = Column(String(50), nullable=True)
    price_min = Column(Integer, nullable=True)
    price_max = Column(Integer, nullable=True)
    sold_quantity = Column(Integer, nullable=True)
    thumb_url = Column(String(500), nullable=True)
    specifications = Column(Text, nullable=True)
    extracted_content = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)
    last_extracted_at = Column(DateTime, default=datetime.now)
    shop = relationship("Shop", backref="product_knowledge")

    def __repr__(self):
        return f"<ProductKnowledge(goods_id='{self.goods_id}', goods_name='{self.goods_name}')>"


class Shop(Base):
    __tablename__ = "shops"
    __table_args__ = (
        UniqueConstraint("channel_id", "shop_id", name="uix_shop_channel_shop"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    channel_id = Column(Integer, ForeignKey("channels.id"), nullable=False)
    shop_id = Column(String(100), nullable=False)
    shop_name = Column(String(100), nullable=False)
    shop_logo = Column(String(255), nullable=True)
    description = Column(String(255))
    channel = relationship("Channel", back_populates="shops")
    accounts = relationship("Account", back_populates="shop", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<Shop(shop_id='{self.shop_id}', shop_name='{self.shop_name}')>"

