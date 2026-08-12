#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
图片编辑程序 - 支持多图、形状、箭头、文字的自由排版
"""

import sys
import os
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QGraphicsView, QGraphicsScene,
    QToolBar, QAction, QFileDialog, QGraphicsItem, QGraphicsPixmapItem,
    QGraphicsEllipseItem, QGraphicsRectItem, QGraphicsTextItem,
    QGraphicsPolygonItem, QColorDialog, QMessageBox,
    QSpinBox, QLabel, QDockWidget, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QSlider, QStatusBar
)
from PyQt5.QtCore import (
    Qt, QRectF, QPointF, QSizeF, QLineF
)
from PyQt5.QtGui import (
    QPen, QBrush, QColor, QFont, QPainter, QPixmap, QImage,
    QPolygonF
)

import math


# ==================== 自定义图形项 ====================

class ResizableHandleMixin:
    """为图形项添加 4 角调整大小 + 4 边裁剪控点"""

    HANDLE_SIZE = 8
    HANDLE_CURSORS = {
        'tl': Qt.SizeFDiagCursor, 'br': Qt.SizeFDiagCursor,
        'tr': Qt.SizeBDiagCursor, 'bl': Qt.SizeBDiagCursor,
        'top': Qt.SizeVerCursor, 'bottom': Qt.SizeVerCursor,
        'left': Qt.SizeHorCursor, 'right': Qt.SizeHorCursor,
    }

    def setup_handles(self, crop=False):
        """创建控点。crop=True 时额外添加 4 边裁剪控点"""
        self._handles = {}
        names = ['tl', 'tr', 'bl', 'br']
        if crop:
            names += ['top', 'bottom', 'left', 'right']
        for name in names:
            handle = HandleItem(self, name)
            handle.setParentItem(self)
            handle.setZValue(1000)
            handle.setVisible(False)
            self._handles[name] = handle
        self.update_handle_positions()

    def _content_rect(self):
        """返回用于放置控点的内容矩形（子类实现）"""
        raise NotImplementedError

    def update_handle_positions(self):
        """将控点定位到四角和四边中点"""
        if not hasattr(self, '_handles'):
            return
        r = self._content_rect()
        positions = {
            'tl': r.topLeft(), 'tr': r.topRight(),
            'bl': r.bottomLeft(), 'br': r.bottomRight(),
            'top': QPointF(r.center().x(), r.top()),
            'bottom': QPointF(r.center().x(), r.bottom()),
            'left': QPointF(r.left(), r.center().y()),
            'right': QPointF(r.right(), r.center().y()),
        }
        for name, handle in self._handles.items():
            handle.setPos(positions[name])

    def set_handles_visible(self, visible):
        if hasattr(self, '_handles'):
            for handle in self._handles.values():
                handle.setVisible(visible)

    def itemChange(self, change, value):
        if change == QGraphicsItem.ItemSelectedChange:
            self.set_handles_visible(bool(value))
        return super().itemChange(change, value)


class HandleItem(QGraphicsRectItem):
    """调整大小 / 裁剪控点（作为父项的父子项）"""

    def __init__(self, target, name):
        self._target = target
        self._name = name
        size = ResizableHandleMixin.HANDLE_SIZE
        super().__init__(-size / 2, -size / 2, size, size)
        self.setBrush(QBrush(QColor(0, 120, 215)))
        self.setPen(QPen(QColor(255, 255, 255), 1))
        self.setAcceptHoverEvents(True)
        self.setCursor(ResizableHandleMixin.HANDLE_CURSORS.get(name, Qt.ArrowCursor))
        self._drag_start = None
        self._start_state = None

    def mousePressEvent(self, event):
        # 在目标项本地坐标中记录拖拽起点
        self._drag_start = self._target.mapFromScene(event.scenePos())
        self._start_state = self._target.get_resize_state()
        event.accept()

    def mouseMoveEvent(self, event):
        if self._drag_start is None:
            return
        cur = self._target.mapFromScene(event.scenePos())
        delta = cur - self._drag_start
        self._target.apply_resize(self._name, delta, self._start_state)
        event.accept()

    def mouseReleaseEvent(self, event):
        self._drag_start = None
        self._start_state = None
        event.accept()


class MovablePixmapItem(ResizableHandleMixin, QGraphicsPixmapItem):
    """可移动的图片项 - 支持四角缩放、四边裁剪、旋转"""
    def __init__(self, pixmap, parent=None):
        super().__init__(pixmap, parent)
        self.setFlags(
            QGraphicsItem.ItemIsMovable |
            QGraphicsItem.ItemIsSelectable |
            QGraphicsItem.ItemSendsGeometryChanges
        )
        self.setAcceptHoverEvents(True)
        self.setTransformationMode(Qt.SmoothTransformation)

        self._original = pixmap.copy()       # 保留原始图像，用于无损缩放/裁剪
        self._crop = [0, 0, 0, 0]            # 裁剪边距: 左、上、右、下 (原图像素)
        self._current_w = pixmap.width()     # 当前显示宽度
        self._current_h = pixmap.height()    # 当前显示高度
        self._apply_display_pixmap()
        self.setup_handles(crop=True)

    # ---------- 控点支持 ----------

    def _content_rect(self):
        return QRectF(0, 0, self._current_w, self._current_h)

    def get_resize_state(self):
        return {
            'w': self._current_w,
            'h': self._current_h,
            'pos': self.pos(),
            'crop': list(self._crop),
        }

    def apply_resize(self, name, delta, state):
        if name in ('left', 'right', 'top', 'bottom'):
            self._apply_crop(name, delta, state)
        else:
            self._apply_scale_resize(name, delta, state)
        self.update_handle_positions()

    def _apply_display_pixmap(self):
        """根据裁剪区域 + 显示尺寸，从原图重新生成当前 pixmap"""
        l, t, r, b = self._crop
        sw = max(1, self._original.width() - l - r)
        sh = max(1, self._original.height() - t - b)
        src = self._original.copy(l, t, sw, sh)
        self.setPixmap(
            src.scaled(int(self._current_w), int(self._current_h),
                       Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
        )

    def _apply_scale_resize(self, name, delta, state):
        """四角拖拽缩放"""
        w, h = state['w'], state['h']
        dx, dy = delta.x(), delta.y()
        offset = QPointF(0, 0)
        if 'l' in name:
            w = max(10, w - dx)
            offset.setX(dx)
        if 'r' in name:
            w = max(10, w + dx)
        if 't' in name:
            h = max(10, h - dy)
            offset.setY(dy)
        if 'b' in name:
            h = max(10, h + dy)
        # 移动位置，保持对角固定
        if offset.x() != 0 or offset.y() != 0:
            origin = self.mapToScene(QPointF(0, 0))
            new_pos = state['pos'] + self.mapToScene(offset) - origin
            self.setPos(new_pos)
        self._current_w, self._current_h = w, h
        self._apply_display_pixmap()

    def _apply_crop(self, name, delta, state):
        """四边拖拽裁剪：直接裁掉对应方向的图像内容，显示尺寸跟随变化（内容不变形）"""
        l, t, r, b = state['crop']
        w, h = state['w'], state['h']
        # 拖拽起点时可见的源区域尺寸（源像素）
        sw = max(1, self._original.width() - l - r)
        sh = max(1, self._original.height() - t - b)
        sx = sw / w   # 源像素 / 显示像素
        sy = sh / h
        dx, dy = delta.x(), delta.y()

        # 向内拖 = 裁掉该方向的内容；向外拖 = 恢复已裁内容
        if name == 'left':
            l += dx * sx
        elif name == 'right':
            r -= dx * sx
        elif name == 'top':
            t += dy * sy
        elif name == 'bottom':
            b -= dy * sy

        # 限制裁剪范围，防止源区域消失（取整供 QPixmap.copy 使用）
        l = int(max(0, min(l, self._original.width() - 1)))
        r = int(max(0, min(r, self._original.width() - 1)))
        t = int(max(0, min(t, self._original.height() - 1)))
        b = int(max(0, min(b, self._original.height() - 1)))
        if self._original.width() - l - r < 1:
            l = max(0, self._original.width() - r - 1)
        if self._original.height() - t - b < 1:
            t = max(0, self._original.height() - b - 1)

        # 显示尺寸跟随变化：新显示尺寸 = 新源可见区域 / 起始缩放比例（内容不拉伸）
        new_sw = max(1, self._original.width() - l - r)
        new_sh = max(1, self._original.height() - t - b)
        w = max(10, int(new_sw / sx))
        h = max(10, int(new_sh / sy))

        self._crop = [int(l), int(t), int(r), int(b)]
        self._current_w, self._current_h = w, h
        self._apply_display_pixmap()

        # 被拖边缘跟随鼠标：向左/向上裁剪时，图片整体向该方向平移，
        # 保持对侧边缘固定（与 right/bottom 行为一致，支持旋转/镜像）
        if name == 'left':
            offset = QPointF(state['w'] - w, 0)
        elif name == 'top':
            offset = QPointF(0, state['h'] - h)
        else:
            offset = None
        if offset is not None and (offset.x() != 0 or offset.y() != 0):
            origin = self.mapToScene(QPointF(0, 0))
            new_pos = state['pos'] + self.mapToScene(offset) - origin
            self.setPos(new_pos)

    def boundingRect(self):
        return super().boundingRect().adjusted(-5, -5, 5, 5)

    def paint(self, painter, option, widget):
        super().paint(painter, option, widget)
        if self.isSelected():
            painter.setPen(QPen(QColor(0, 120, 215), 2, Qt.DashLine))
            painter.setBrush(Qt.NoBrush)
            painter.drawRect(self.boundingRect().adjusted(3, 3, -3, -3))


class MovableEllipseItem(ResizableHandleMixin, QGraphicsEllipseItem):
    """可移动的圆形/椭圆 - 支持四角调整大小"""
    def __init__(self, rect, parent=None):
        super().__init__(rect, parent)
        self.setFlags(
            QGraphicsItem.ItemIsMovable |
            QGraphicsItem.ItemIsSelectable |
            QGraphicsItem.ItemSendsGeometryChanges
        )
        self.setAcceptHoverEvents(True)
        pen = QPen(QColor(200, 50, 50), 2)
        self.setPen(pen)
        self.setBrush(QBrush(QColor(200, 50, 50, 80)))
        self.setup_handles(crop=False)

    # ---------- 控点支持 ----------

    def _content_rect(self):
        return self.rect()

    def get_resize_state(self):
        return {'rect': self.rect()}

    def apply_resize(self, name, delta, state):
        r = QRectF(state['rect'])
        dx, dy = delta.x(), delta.y()
        if 'l' in name:
            r.setLeft(r.left() + dx)
        if 'r' in name:
            r.setRight(r.right() + dx)
        if 't' in name:
            r.setTop(r.top() + dy)
        if 'b' in name:
            r.setBottom(r.bottom() + dy)
        # 限制最小尺寸
        if r.width() < 20:
            if 'l' in name:
                r.setLeft(r.right() - 20)
            else:
                r.setRight(r.left() + 20)
        if r.height() < 20:
            if 't' in name:
                r.setTop(r.bottom() - 20)
            else:
                r.setBottom(r.top() + 20)
        self.setRect(r)
        self.update_handle_positions()

    def boundingRect(self):
        return super().boundingRect().adjusted(-4, -4, 4, 4)

    def paint(self, painter, option, widget):
        super().paint(painter, option, widget)
        if self.isSelected():
            painter.setPen(QPen(QColor(0, 120, 215), 2, Qt.DashLine))
            painter.setBrush(Qt.NoBrush)
            painter.drawRect(self.boundingRect().adjusted(3, 3, -3, -3))


class MovableRectItem(ResizableHandleMixin, QGraphicsRectItem):
    """可移动的方形/矩形 - 支持四角调整大小"""
    def __init__(self, rect, parent=None):
        super().__init__(rect, parent)
        self.setFlags(
            QGraphicsItem.ItemIsMovable |
            QGraphicsItem.ItemIsSelectable |
            QGraphicsItem.ItemSendsGeometryChanges
        )
        self.setAcceptHoverEvents(True)
        pen = QPen(QColor(50, 150, 50), 2)
        self.setPen(pen)
        self.setBrush(QBrush(QColor(50, 150, 50, 80)))
        self.setup_handles(crop=False)

    # ---------- 控点支持 ----------

    def _content_rect(self):
        return self.rect()

    def get_resize_state(self):
        return {'rect': self.rect()}

    def apply_resize(self, name, delta, state):
        r = QRectF(state['rect'])
        dx, dy = delta.x(), delta.y()
        if 'l' in name:
            r.setLeft(r.left() + dx)
        if 'r' in name:
            r.setRight(r.right() + dx)
        if 't' in name:
            r.setTop(r.top() + dy)
        if 'b' in name:
            r.setBottom(r.bottom() + dy)
        if r.width() < 20:
            if 'l' in name:
                r.setLeft(r.right() - 20)
            else:
                r.setRight(r.left() + 20)
        if r.height() < 20:
            if 't' in name:
                r.setTop(r.bottom() - 20)
            else:
                r.setBottom(r.top() + 20)
        self.setRect(r)
        self.update_handle_positions()

    def boundingRect(self):
        return super().boundingRect().adjusted(-4, -4, 4, 4)

    def paint(self, painter, option, widget):
        super().paint(painter, option, widget)
        if self.isSelected():
            painter.setPen(QPen(QColor(0, 120, 215), 2, Qt.DashLine))
            painter.setBrush(Qt.NoBrush)
            painter.drawRect(self.boundingRect().adjusted(3, 3, -3, -3))


class ArrowItem(ResizableHandleMixin, QGraphicsPolygonItem):
    """可移动的箭头 - 支持四角调整大小"""
    def __init__(self, start, end, parent=None):
        self._start = start
        self._end = end
        super().__init__(parent)
        self.setFlags(
            QGraphicsItem.ItemIsMovable |
            QGraphicsItem.ItemIsSelectable |
            QGraphicsItem.ItemSendsGeometryChanges
        )
        self.setAcceptHoverEvents(True)
        pen = QPen(QColor(200, 120, 30), 3)
        self.setPen(pen)
        self.setBrush(QBrush(QColor(200, 120, 30, 180)))
        self._build_arrow(start, end)
        self._draw_shaft = True
        self._pen_color = QColor(200, 120, 30)
        self._pen_width = 3
        self.setup_handles(crop=False)

    # ---------- 控点支持 ----------

    def _content_rect(self):
        return QRectF(
            min(self._start.x(), self._end.x()),
            min(self._start.y(), self._end.y()),
            abs(self._end.x() - self._start.x()),
            abs(self._end.y() - self._start.y())
        )

    def get_resize_state(self):
        return {'start': QPointF(self._start), 'end': QPointF(self._end)}

    def apply_resize(self, name, delta, state):
        s = QPointF(state['start'])
        e = QPointF(state['end'])
        old_rect = QRectF(s, e).normalized()
        new_rect = QRectF(old_rect)
        dx, dy = delta.x(), delta.y()
        if 'l' in name:
            new_rect.setLeft(old_rect.left() + dx)
        if 'r' in name:
            new_rect.setRight(old_rect.right() + dx)
        if 't' in name:
            new_rect.setTop(old_rect.top() + dy)
        if 'b' in name:
            new_rect.setBottom(old_rect.bottom() + dy)
        if new_rect.width() < 20:
            if 'l' in name:
                new_rect.setLeft(old_rect.left())
            else:
                new_rect.setRight(old_rect.right())
        if new_rect.height() < 20:
            if 't' in name:
                new_rect.setTop(old_rect.top())
            else:
                new_rect.setBottom(old_rect.bottom())

        # 以中心缩放两个端点
        center = (s + e) / 2
        sx = new_rect.width() / old_rect.width() if old_rect.width() else 1
        sy = new_rect.height() / old_rect.height() if old_rect.height() else 1
        s2 = center + QPointF((s.x() - center.x()) * sx, (s.y() - center.y()) * sy)
        e2 = center + QPointF((e.x() - center.x()) * sx, (e.y() - center.y()) * sy)
        self.set_ends(s2, e2)
        self.update_handle_positions()

    def _build_arrow(self, start, end):
        """构建箭头多边形"""
        line = QLineF(start, end)
        length = line.length()
        if length < 1:
            length = 1

        arrow_size = min(15, length * 0.3)
        angle = math.atan2(-line.dy(), line.dx())

        # 箭头头部三角形（线段的终点端）
        p1 = end
        p2 = QPointF(
            end.x() - arrow_size * math.cos(angle - math.pi / 6),
            end.y() + arrow_size * math.sin(angle - math.pi / 6)
        )
        p3 = QPointF(
            end.x() - arrow_size * math.cos(angle + math.pi / 6),
            end.y() + arrow_size * math.sin(angle + math.pi / 6)
        )

        polygon = QPolygonF([p1, p2, p3])
        self.setPolygon(polygon)

        self._shaft_line = QLineF(start, end)
        self._arrow_size = arrow_size
        self._angle = angle

    def set_color(self, color):
        self._pen_color = color
        pen = QPen(color, self._pen_width)
        self.setPen(pen)
        self.setBrush(QBrush(QColor(color.red(), color.green(), color.blue(), 180)))

    def set_line_width(self, width):
        self._pen_width = width
        pen = QPen(self._pen_color, width)
        self.setPen(pen)

    def boundingRect(self):
        br = super().boundingRect()
        if self._draw_shaft:
            br = br.united(QRectF(self._start, self._end).normalized())
        return br.adjusted(-8, -8, 8, 8)

    def paint(self, painter, option, widget):
        # 画箭杆
        if self._draw_shaft:
            painter.setPen(QPen(self._pen_color, self._pen_width, Qt.SolidLine, Qt.RoundCap))
            painter.setBrush(Qt.NoBrush)
            painter.drawLine(self._shaft_line)

        # 画箭头头部三角形
        painter.setPen(QPen(self._pen_color, self._pen_width, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        painter.setBrush(QBrush(QColor(self._pen_color.red(), self._pen_color.green(),
                                       self._pen_color.blue(), 200)))
        painter.drawPolygon(self.polygon())

        if self.isSelected():
            painter.setPen(QPen(QColor(0, 120, 215), 2, Qt.DashLine))
            painter.setBrush(Qt.NoBrush)
            painter.drawRect(self.boundingRect().adjusted(4, 4, -4, -4))

    def set_ends(self, start, end):
        self._start = start
        self._end = end
        self._build_arrow(start, end)
        self.update()
        self.prepareGeometryChange()


class MovableTextItem(QGraphicsTextItem):
    """可移动的文字项"""
    def __init__(self, text, parent=None):
        super().__init__(text, parent)
        self.setFlags(
            QGraphicsItem.ItemIsMovable |
            QGraphicsItem.ItemIsSelectable |
            QGraphicsItem.ItemSendsGeometryChanges
        )
        self.setAcceptHoverEvents(True)
        font = QFont("Microsoft YaHei", 20)
        font.setBold(False)
        self.setFont(font)
        self.setDefaultTextColor(QColor(0, 0, 0))
        self.setTextInteractionFlags(Qt.TextEditorInteraction)

    def boundingRect(self):
        return super().boundingRect().adjusted(-4, -4, 4, 4)

    def paint(self, painter, option, widget):
        super().paint(painter, option, widget)
        if self.isSelected():
            painter.setPen(QPen(QColor(0, 120, 215), 2, Qt.DashLine))
            painter.setBrush(Qt.NoBrush)
            painter.drawRect(self.boundingRect().adjusted(3, 3, -3, -3))


# ==================== 自定义场景 ====================

class EditorScene(QGraphicsScene):
    """编辑器场景 - 处理拖放等"""
    grid_size = 20

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSceneRect(QRectF(-2000, -2000, 4000, 4000))

    def drawBackground(self, painter, rect):
        """绘制网格背景"""
        painter.fillRect(rect, QColor(245, 245, 245))

        # 画网格
        painter.setPen(QPen(QColor(220, 220, 220), 0.5))
        left = int(rect.left()) - (int(rect.left()) % self.grid_size)
        top = int(rect.top()) - (int(rect.top()) % self.grid_size)

        lines = []
        for x in range(left, int(rect.right()), self.grid_size):
            lines.append(QLineF(x, rect.top(), x, rect.bottom()))
        for y in range(top, int(rect.bottom()), self.grid_size):
            lines.append(QLineF(rect.left(), y, rect.right(), y))

        painter.drawLines(lines)

        # 画坐标轴
        painter.setPen(QPen(QColor(180, 180, 180), 1))
        painter.drawLine(QLineF(0, rect.top(), 0, rect.bottom()))
        painter.drawLine(QLineF(rect.left(), 0, rect.right(), 0))


# ==================== 自定义视图 ====================

class EditorView(QGraphicsView):
    """编辑器视图 - 支持缩放和平移"""
    def __init__(self, scene, parent=None):
        super().__init__(scene, parent)
        self.setRenderHints(
            QPainter.Antialiasing | QPainter.SmoothPixmapTransform
        )
        self.setDragMode(QGraphicsView.RubberBandDrag)
        self.setViewportUpdateMode(QGraphicsView.SmartViewportUpdate)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.AnchorUnderMouse)
        self._zoom = 0
        self._panning = False

    def wheelEvent(self, event):
        """滚轮缩放"""
        factor = 1.15
        if event.angleDelta().y() > 0:
            if self._zoom < 30:
                self.scale(factor, factor)
                self._zoom += 1
        else:
            if self._zoom > -15:
                self.scale(1 / factor, 1 / factor)
                self._zoom -= 1

    def mousePressEvent(self, event):
        """鼠标中键拖拽"""
        if event.button() == Qt.MiddleButton:
            self._panning = True
            self._pan_start = event.pos()
            self.setCursor(Qt.ClosedHandCursor)
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._panning:
            delta = event.pos() - self._pan_start
            self._pan_start = event.pos()
            self.horizontalScrollBar().setValue(
                self.horizontalScrollBar().value() - delta.x()
            )
            self.verticalScrollBar().setValue(
                self.verticalScrollBar().value() - delta.y()
            )
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MiddleButton:
            self._panning = False
            self.setCursor(Qt.ArrowCursor)
            event.accept()
        else:
            super().mouseReleaseEvent(event)

    def reset_zoom(self):
        """重置缩放"""
        self.resetTransform()
        self._zoom = 0


# ==================== 带点击检测的视图 ====================

class ClickableEditorView(EditorView):
    """可点击添加对象的视图"""

    def __init__(self, scene, editor, parent=None):
        super().__init__(scene, parent)
        self._editor = editor

    def mousePressEvent(self, event):
        # 右键：阻止事件传递到 QGraphicsView，避免 RubberBandDrag 冲突导致闪退
        if event.button() == Qt.RightButton:
            return

        if event.button() == Qt.LeftButton:
            item = self.itemAt(event.pos())
            if item is not None:
                # 点击了已有图层 -> 自动切换为选择模式，用于移动/编辑
                if self._editor._current_tool != "select":
                    self._editor._set_tool("select", self._editor._act_select)
            else:
                # 点击空白区域
                if self._editor._current_tool != "select":
                    # 有绘制工具激活 -> 在点击位置添加对应对象
                    scene_pos = self.mapToScene(event.pos())
                    self._editor._handle_scene_click(scene_pos)
                    return

        super().mousePressEvent(event)


# ==================== 主窗口 ====================

class ImageEditor(QMainWindow):
    """图片编辑主窗口"""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("图片编辑器 - 图表编辑工具")
        self.setWindowState(Qt.WindowMaximized)
        self.setMinimumSize(1000, 700)

        # 初始化场景
        self._scene = EditorScene()
        # 使用可点击视图（支持在空白处点击添加对象）
        self._view = ClickableEditorView(self._scene, self)
        self.setCentralWidget(self._view)

        # 当前工具模式
        self._current_tool = "select"
        self._last_fill_color = QColor(200, 50, 50, 80)  # 缓存最后的填充颜色
        self._fill_is_transparent = False

        self._setup_ui()
        self._setup_statusbar()

    def _setup_ui(self):
        """创建界面"""
        self._create_toolbar()
        self._create_properties_dock()

    def _create_toolbar(self):
        """创建工具栏"""
        toolbar = QToolBar("工具", self)
        toolbar.setMovable(False)
        toolbar.setIconSize(QSizeF(24, 24).toSize())
        self.addToolBar(Qt.TopToolBarArea, toolbar)

        # 选择工具
        act_select = QAction("🖱 选择", self)
        act_select.setToolTip("选择和移动对象 (S)")
        act_select.setShortcut("S")
        act_select.setCheckable(True)
        act_select.setChecked(True)
        act_select.triggered.connect(lambda: self._set_tool("select", act_select))
        toolbar.addAction(act_select)
        self._act_select = act_select

        toolbar.addSeparator()

        # 添加图片
        act_image = QAction("🖼 添加图片", self)
        act_image.setToolTip("从文件添加图片 (I)")
        act_image.setShortcut("I")
        act_image.triggered.connect(self._add_images)
        toolbar.addAction(act_image)

        toolbar.addSeparator()

        # 圆形
        act_circle = QAction("⭕ 圆形", self)
        act_circle.setToolTip("添加圆形/椭圆 (C)")
        act_circle.setShortcut("C")
        act_circle.setCheckable(True)
        act_circle.triggered.connect(lambda: self._set_tool("circle", act_circle))
        toolbar.addAction(act_circle)
        self._act_circle = act_circle

        # 方形
        act_rect = QAction("⬜ 方形", self)
        act_rect.setToolTip("添加矩形 (R)")
        act_rect.setShortcut("R")
        act_rect.setCheckable(True)
        act_rect.triggered.connect(lambda: self._set_tool("rect", act_rect))
        toolbar.addAction(act_rect)
        self._act_rect = act_rect

        # 箭头
        act_arrow = QAction("➡ 箭头", self)
        act_arrow.setToolTip("添加箭头 (A)")
        act_arrow.setShortcut("A")
        act_arrow.setCheckable(True)
        act_arrow.triggered.connect(lambda: self._set_tool("arrow", act_arrow))
        toolbar.addAction(act_arrow)
        self._act_arrow = act_arrow

        toolbar.addSeparator()

        # 文字
        act_text = QAction("📝 文字", self)
        act_text.setToolTip("添加文字 (T)")
        act_text.setShortcut("T")
        act_text.setCheckable(True)
        act_text.triggered.connect(lambda: self._set_tool("text", act_text))
        toolbar.addAction(act_text)
        self._act_text = act_text

        toolbar.addSeparator()

        # 删除
        act_delete = QAction("🗑 删除", self)
        act_delete.setToolTip("删除选中对象 (Delete)")
        act_delete.setShortcut("Delete")
        act_delete.triggered.connect(self._delete_selected)
        toolbar.addAction(act_delete)

        toolbar.addSeparator()

        # 导出
        act_export = QAction("💾 导出图片", self)
        act_export.setToolTip("导出为图片文件 (Ctrl+E)")
        act_export.setShortcut("Ctrl+E")
        act_export.triggered.connect(self._export_image)
        toolbar.addAction(act_export)

        # 清空
        act_clear = QAction("🧹 清空", self)
        act_clear.setToolTip("清空画布")
        act_clear.triggered.connect(self._clear_scene)
        toolbar.addAction(act_clear)

        toolbar.addSeparator()

        # 适应窗口
        act_fit = QAction("🔍 适合窗口", self)
        act_fit.setToolTip("将场景适应窗口显示")
        act_fit.setShortcut("Ctrl+0")
        act_fit.triggered.connect(self._fit_view)
        toolbar.addAction(act_fit)

        # 保存布局
        act_save_layout = QAction("📂 保存布局", self)
        act_save_layout.setToolTip("保存布局文件 (Ctrl+S)")
        act_save_layout.setShortcut("Ctrl+S")
        act_save_layout.triggered.connect(self._save_layout)
        toolbar.addAction(act_save_layout)

        # 打开布局
        act_open_layout = QAction("📂 打开布局", self)
        act_open_layout.setToolTip("打开布局文件 (Ctrl+O)")
        act_open_layout.setShortcut("Ctrl+O")
        act_open_layout.triggered.connect(self._open_layout)
        toolbar.addAction(act_open_layout)

    def _create_properties_dock(self):
        """创建属性面板"""
        dock = QDockWidget("对象属性", self)
        dock.setAllowedAreas(Qt.LeftDockWidgetArea | Qt.RightDockWidgetArea)
        dock.setMaximumWidth(220)

        widget = QWidget()
        layout = QVBoxLayout(widget)

        self._prop_label = QLabel("选择一个对象以编辑属性")
        self._prop_label.setWordWrap(True)
        layout.addWidget(self._prop_label)

        # 颜色按钮
        layout.addWidget(QLabel("填充颜色:"))
        btn_fill = QPushButton("设置填充色")
        btn_fill.clicked.connect(self._set_fill_color)
        layout.addWidget(btn_fill)
        self._btn_fill = btn_fill

        # 透明填充
        self._transparent_check = QPushButton("透明填充: 关")
        self._transparent_check.clicked.connect(self._toggle_fill_transparent)
        layout.addWidget(self._transparent_check)

        layout.addWidget(QLabel("边框颜色:"))
        btn_pen = QPushButton("设置边框色")
        btn_pen.clicked.connect(self._set_pen_color)
        layout.addWidget(btn_pen)
        self._btn_pen = btn_pen

        layout.addWidget(QLabel("文字颜色:"))
        btn_text_color = QPushButton("设置文字颜色")
        btn_text_color.clicked.connect(self._set_text_color)
        layout.addWidget(btn_text_color)
        self._btn_text_color = btn_text_color

        # 字体大小
        layout.addWidget(QLabel("字体大小:"))
        size_layout = QHBoxLayout()
        self._font_spin = QSpinBox()
        self._font_spin.setRange(6, 200)
        self._font_spin.setValue(20)
        self._font_spin.valueChanged.connect(self._set_font_size)
        size_layout.addWidget(self._font_spin)

        btn_font_bold = QPushButton("粗体")
        btn_font_bold.setCheckable(True)
        btn_font_bold.toggled.connect(self._set_font_bold)
        size_layout.addWidget(btn_font_bold)
        self._btn_font_bold = btn_font_bold
        layout.addLayout(size_layout)

        # 边框宽度
        layout.addWidget(QLabel("边框宽度:"))
        self._pen_spin = QSpinBox()
        self._pen_spin.setRange(1, 20)
        self._pen_spin.setValue(2)
        self._pen_spin.valueChanged.connect(self._set_pen_width)
        layout.addWidget(self._pen_spin)

        # 透明度
        layout.addWidget(QLabel("透明度:"))
        self._opacity_slider = QSlider(Qt.Horizontal)
        self._opacity_slider.setRange(10, 100)
        self._opacity_slider.setValue(100)
        self._opacity_slider.valueChanged.connect(self._set_opacity)
        layout.addWidget(self._opacity_slider)

        # 层级控制
        layout.addWidget(QLabel("层级:"))
        z_layout = QHBoxLayout()
        btn_front = QPushButton("置顶")
        btn_front.clicked.connect(self._bring_to_front)
        z_layout.addWidget(btn_front)
        btn_back = QPushButton("置底")
        btn_back.clicked.connect(self._send_to_back)
        z_layout.addWidget(btn_back)
        layout.addLayout(z_layout)

        # 旋转
        layout.addWidget(QLabel("旋转角度:"))
        rot_layout = QHBoxLayout()
        self._rot_spin = QSpinBox()
        self._rot_spin.setRange(-360, 360)
        self._rot_spin.setValue(0)
        self._rot_spin.valueChanged.connect(self._set_rotation)
        rot_layout.addWidget(self._rot_spin)
        layout.addLayout(rot_layout)

        # 缩放
        layout.addWidget(QLabel("缩放比例 (%):"))
        scale_layout = QHBoxLayout()
        self._scale_spin = QSpinBox()
        self._scale_spin.setRange(10, 500)
        self._scale_spin.setValue(100)
        self._scale_spin.valueChanged.connect(self._set_scale)
        scale_layout.addWidget(self._scale_spin)
        layout.addLayout(scale_layout)

        layout.addStretch()

        widget.setLayout(layout)
        dock.setWidget(widget)
        self.addDockWidget(Qt.RightDockWidgetArea, dock)

        # 监听选择变化
        self._scene.selectionChanged.connect(self._on_selection_changed)

    def _setup_statusbar(self):
        """状态栏"""
        self._statusbar = QStatusBar()
        self.setStatusBar(self._statusbar)
        self._status_label = QLabel("就绪 | 鼠标滚轮缩放 | 中键拖拽平移 | 快捷键: S选择 I图片 C圆 R方 A箭头 T文字")
        self._statusbar.addWidget(self._status_label)

    # ==================== 工具切换 ====================

    def _set_tool(self, tool, action):
        """切换工具"""
        tool_actions = {
            "select": self._act_select,
            "circle": self._act_circle,
            "rect": self._act_rect,
            "arrow": self._act_arrow,
            "text": self._act_text,
        }

        for t, act in tool_actions.items():
            if act is not None:
                act.setChecked(t == tool)

        self._current_tool = tool

        if tool == "select":
            self._view.setDragMode(QGraphicsView.RubberBandDrag)
            self._view.setCursor(Qt.ArrowCursor)
        else:
            self._view.setDragMode(QGraphicsView.NoDrag)
            if tool in ("circle", "rect", "arrow"):
                self._view.setCursor(Qt.CrossCursor)
            elif tool == "text":
                self._view.setCursor(Qt.IBeamCursor)

        self._status_label.setText(f"当前工具: {tool} | 点击画布添加 | 鼠标滚轮缩放 | 中键拖拽平移")

    # ==================== 视图事件处理 ====================

    def _handle_scene_click(self, scene_pos):
        """处理场景点击 - 添加对象"""
        if self._current_tool == "select":
            return
        elif self._current_tool == "circle":
            self._add_circle_at(scene_pos)
        elif self._current_tool == "rect":
            self._add_rect_at(scene_pos)
        elif self._current_tool == "arrow":
            self._add_arrow_at(scene_pos)
        elif self._current_tool == "text":
            self._add_text_at(scene_pos)

    def _add_circle_at(self, pos):
        """在指定位置添加圆形"""
        r = 60
        rect = QRectF(pos.x() - r, pos.y() - r, r * 2, r * 2)
        item = MovableEllipseItem(rect)
        self._scene.addItem(item)
        self._status_label.setText(f"添加圆形于 ({pos.x():.0f}, {pos.y():.0f})")

    def _add_rect_at(self, pos):
        """在指定位置添加方形"""
        w, h = 100, 80
        rect = QRectF(pos.x() - w / 2, pos.y() - h / 2, w, h)
        item = MovableRectItem(rect)
        self._scene.addItem(item)
        self._status_label.setText(f"添加矩形于 ({pos.x():.0f}, {pos.y():.0f})")

    def _add_arrow_at(self, pos):
        """在指定位置添加箭头"""
        start = QPointF(pos.x() - 80, pos.y())
        end = QPointF(pos.x() + 80, pos.y())
        item = ArrowItem(start, end)
        self._scene.addItem(item)
        self._status_label.setText(f"添加箭头于 ({pos.x():.0f}, {pos.y():.0f})")

    def _add_text_at(self, pos):
        """在指定位置添加文字"""
        item = MovableTextItem("双击编辑文字")
        item.setPos(pos)
        self._scene.addItem(item)
        self._status_label.setText(f"添加文字于 ({pos.x():.0f}, {pos.y():.0f})")

    # ==================== 图片操作 ====================

    def _add_images(self):
        """添加图片文件"""
        file_paths, _ = QFileDialog.getOpenFileNames(
            self, "选择图片",
            "",
            "图片文件 (*.png *.jpg *.jpeg *.bmp *.gif *.webp);;所有文件 (*.*)"
        )
        if not file_paths:
            return

        count = 0
        center = self._view.mapToScene(self._view.viewport().rect().center())

        for i, path in enumerate(file_paths):
            pixmap = QPixmap(path)
            if pixmap.isNull():
                QMessageBox.warning(self, "警告", f"无法加载图片: {os.path.basename(path)}")
                continue

            # 限制初始大小
            max_size = 400
            if pixmap.width() > max_size or pixmap.height() > max_size:
                pixmap = pixmap.scaled(
                    max_size, max_size,
                    Qt.KeepAspectRatio,
                    Qt.SmoothTransformation
                )

            item = MovablePixmapItem(pixmap)
            # 偏移位置防止重叠
            offset_x = (i % 3) * 120 - 120
            offset_y = (i // 3) * 100 - 50
            item.setPos(center.x() + offset_x - pixmap.width() / 2,
                        center.y() + offset_y - pixmap.height() / 2)
            self._scene.addItem(item)
            count += 1

        self._status_label.setText(f"已添加 {count} 张图片")
        # 添加完后切回选择工具
        self._set_tool("select", self._act_select)

    # ==================== 删除操作 ====================

    def _delete_selected(self):
        """删除选中的对象"""
        items = self._scene.selectedItems()
        if not items:
            return
        reply = QMessageBox.question(
            self, "确认删除",
            f"确定要删除选中的 {len(items)} 个对象吗？",
            QMessageBox.Yes | QMessageBox.No
        )
        if reply == QMessageBox.Yes:
            for item in items:
                self._scene.removeItem(item)
            self._status_label.setText(f"已删除 {len(items)} 个对象")

    # ==================== 清空场景 ====================

    def _clear_scene(self):
        """清空画布"""
        reply = QMessageBox.question(
            self, "确认清空",
            "确定要清空所有内容吗？此操作不可撤销！",
            QMessageBox.Yes | QMessageBox.No
        )
        if reply == QMessageBox.Yes:
            self._scene.clear()
            self._status_label.setText("画布已清空")

    # ==================== 选择变化处理 ====================

    def _on_selection_changed(self):
        """选择变化时更新属性面板"""
        items = self._scene.selectedItems()
        if len(items) == 1:
            item = items[0]
            names = {
                MovablePixmapItem: "图片",
                MovableEllipseItem: "圆形",
                MovableRectItem: "矩形",
                ArrowItem: "箭头",
                MovableTextItem: "文字",
            }
            name = names.get(type(item), type(item).__name__)
            self._prop_label.setText(f"选中: {name}")

            # 同步透明填充按钮状态
            if hasattr(item, 'brush'):
                brush_color = item.brush().color()
                self._fill_is_transparent = (brush_color.alpha() == 0)
                text = "透明填充: 开" if self._fill_is_transparent else "透明填充: 关"
                self._transparent_check.setText(text)
        elif len(items) > 1:
            self._prop_label.setText(f"选中: {len(items)} 个对象")
        else:
            self._prop_label.setText("选择一个对象以编辑属性")

    def _get_single_selected(self):
        """获取单个选定对象"""
        items = self._scene.selectedItems()
        if len(items) == 1:
            return items[0]
        return None

    # ==================== 属性编辑 ====================

    def _set_fill_color(self):
        """设置填充颜色"""
        item = self._get_single_selected()
        if item is None:
            return
        color = QColorDialog.getColor()
        if not color.isValid():
            return
        if hasattr(item, 'setBrush'):
            alpha = 80
            self._last_fill_color = QColor(color.red(), color.green(), color.blue(), alpha)
            item.setBrush(QBrush(self._last_fill_color))
            self._fill_is_transparent = False
            self._transparent_check.setText("透明填充: 关")
        self._status_label.setText(f"已设置填充颜色: {color.name()}")

    def _toggle_fill_transparent(self):
        """点击切换透明填充（用透明色代替 NoBrush，避免闪退）"""
        item = self._get_single_selected()
        if item is None:
            return
        if not hasattr(item, 'setBrush'):
            return

        is_transparent = getattr(self, '_fill_is_transparent', False)

        if is_transparent:
            # 恢复颜色
            color = getattr(self, '_last_fill_color', QColor(200, 50, 50, 80))
            item.setBrush(QBrush(color))
            self._fill_is_transparent = False
            self._transparent_check.setText("透明填充: 关")
            self._status_label.setText("填充已恢复")
        else:
            # 设为透明：保存颜色，用透明色填充
            self._last_fill_color = item.brush().color()
            item.setBrush(QBrush(QColor(0, 0, 0, 0)))
            self._fill_is_transparent = True
            self._transparent_check.setText("透明填充: 开")
            self._status_label.setText("填充已设为透明")

    def _set_pen_color(self):
        """设置边框颜色"""
        item = self._get_single_selected()
        if item is None:
            return
        color = QColorDialog.getColor()
        if not color.isValid():
            return

        if isinstance(item, ArrowItem):
            item.set_color(color)
        elif hasattr(item, 'setPen'):
            pen = item.pen()
            pen.setColor(color)
            item.setPen(pen)
        elif hasattr(item, 'setDefaultTextColor'):
            item.setDefaultTextColor(color)
        self._status_label.setText(f"已设置边框/线条颜色: {color.name()}")

    def _set_text_color(self):
        """设置文字颜色"""
        item = self._get_single_selected()
        if item is None:
            return
        if isinstance(item, MovableTextItem):
            color = QColorDialog.getColor()
            if color.isValid():
                item.setDefaultTextColor(color)
                self._status_label.setText(f"已设置文字颜色: {color.name()}")

    def _set_font_size(self, size):
        """设置字体大小"""
        item = self._get_single_selected()
        if item is None:
            return
        if isinstance(item, MovableTextItem):
            font = item.font()
            font.setPointSize(size)
            item.setFont(font)

    def _set_font_bold(self, checked):
        """设置粗体"""
        item = self._get_single_selected()
        if item is None:
            return
        if isinstance(item, MovableTextItem):
            font = item.font()
            font.setBold(checked)
            item.setFont(font)

    def _set_pen_width(self, width):
        """设置边框宽度"""
        item = self._get_single_selected()
        if item is None:
            return
        if isinstance(item, ArrowItem):
            item.set_line_width(width)
        elif hasattr(item, 'setPen'):
            pen = item.pen()
            pen.setWidth(width)
            item.setPen(pen)

    def _set_opacity(self, value):
        """设置透明度"""
        item = self._get_single_selected()
        if item is None:
            return
        item.setOpacity(value / 100.0)

    def _bring_to_front(self):
        """置顶"""
        item = self._get_single_selected()
        if item:
            max_z = 0
            for other in self._scene.items():
                if other.zValue() > max_z:
                    max_z = other.zValue()
            item.setZValue(max_z + 1)
            self._status_label.setText("对象已置顶")

    def _send_to_back(self):
        """置底"""
        item = self._get_single_selected()
        if item:
            min_z = 0
            for other in self._scene.items():
                if other.zValue() < min_z:
                    min_z = other.zValue()
            item.setZValue(min_z - 1)
            self._status_label.setText("对象已置底")

    def _set_rotation(self, angle):
        """设置旋转角度"""
        item = self._get_single_selected()
        if item is None:
            return
        center = item.boundingRect().center()
        item.setTransformOriginPoint(center)
        item.setRotation(angle)

    def _set_scale(self, percent):
        """设置缩放比例"""
        item = self._get_single_selected()
        if item is None:
            return
        factor = percent / 100.0
        center = item.boundingRect().center()
        item.setTransformOriginPoint(center)
        item.setScale(factor)

    # ==================== 视图操作 ====================

    def _fit_view(self):
        """适应窗口"""
        self._view.fitInView(self._scene.sceneRect(), Qt.KeepAspectRatio)
        self._view.reset_zoom()

    # ==================== 导出/保存 ====================

    def _export_image(self):
        """导出为图片"""
        file_path, _ = QFileDialog.getSaveFileName(
            self, "导出图片",
            "output.png",
            "PNG (*.png);;JPEG (*.jpg *.jpeg);;BMP (*.bmp)"
        )
        if not file_path:
            return

        # 计算所有项的边界
        items = self._scene.items()
        if not items:
            QMessageBox.warning(self, "警告", "画布为空，请先添加内容")
            return

        rect = QRectF()
        for item in items:
            br = item.sceneBoundingRect()
            rect = rect.united(br)

        margin = 20
        rect.adjust(-margin, -margin, margin, margin)

        # 渲染到图片
        dpi_scale = 2  # 2x 导出
        image = QImage(
            int(rect.width() * dpi_scale),
            int(rect.height() * dpi_scale),
            QImage.Format_ARGB32_Premultiplied
        )
        image.fill(Qt.white)

        painter = QPainter(image)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        painter.scale(dpi_scale, dpi_scale)

        self._scene.render(painter, QRectF(), rect)
        painter.end()

        if image.save(file_path):
            self._status_label.setText(f"已导出: {file_path}")
            QMessageBox.information(self, "成功", f"图片已保存到:\n{file_path}")
        else:
            QMessageBox.critical(self, "错误", "保存失败")

    def _save_layout(self):
        """保存布局（同导出图片）"""
        self._export_image()

    def _open_layout(self):
        """打开布局 (添加图片到当前场景)"""
        self._add_images()

    # ==================== 键盘事件 ====================

    def keyPressEvent(self, event):
        """全局键盘快捷键"""
        # Ctrl+C 复制坐标
        if event.key() == Qt.Key_Escape:
            self._set_tool("select", self._act_select)
            self._scene.clearSelection()
        else:
            super().keyPressEvent(event)




# ==================== 入口 ====================

def main():
    app = QApplication(sys.argv)
    app.setApplicationName("图片编辑器")

    # 全局样式
    app.setStyle("Fusion")
    stylesheet = """
    QMainWindow {
        background-color: #f0f0f0;
    }
    QToolBar {
        background-color: #e8e8e8;
        border-bottom: 1px solid #ccc;
        padding: 4px;
        spacing: 4px;
    }
    QToolBar QToolButton {
        padding: 6px 12px;
        border-radius: 4px;
        border: 1px solid transparent;
    }
    QToolBar QToolButton:hover {
        background-color: #d0d0d0;
        border-color: #aaa;
    }
    QToolBar QToolButton:checked {
        background-color: #0078d4;
        color: white;
        border-color: #005a9e;
    }
    QDockWidget {
        font-size: 13px;
    }
    QPushButton {
        padding: 5px 10px;
        border-radius: 3px;
        border: 1px solid #aaa;
        background-color: #f5f5f5;
    }
    QPushButton:hover {
        background-color: #e0e0e0;
    }
    QSpinBox {
        padding: 3px;
        border-radius: 3px;
        border: 1px solid #aaa;
    }
    """
    app.setStyleSheet(stylesheet)

    editor = ImageEditor()
    editor.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
