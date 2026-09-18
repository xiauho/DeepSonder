"""Embedded, keyboard-accessible author-ordered story timeline."""
from PySide6.QtCore import QAbstractListModel, QModelIndex, QSize, Qt, Signal, QRectF
from PySide6.QtGui import QColor, QPainter, QPen, QPalette
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QComboBox, QCheckBox, QPushButton, QListView, QStyledItemDelegate, QStyle,
    QSplitter, QPlainTextEdit, QDialog, QFormLayout, QMessageBox, QInputDialog,
    QAbstractItemView, QApplication)
from core.timeline_events import TimelineStore, STATES, USES
from ui.timeline_dialog import EventEditorDialog, combo, buttons


class EventListModel(QAbstractListModel):
    EventRole = int(Qt.ItemDataRole.UserRole) + 1

    def __init__(self, parent=None):
        super().__init__(parent)
        self.events = []

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.events)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or not 0 <= index.row() < len(self.events):
            return None
        event = self.events[index.row()]
        if role == self.EventRole:
            return event
        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.AccessibleTextRole):
            return f"{event['time'] or '时间待定'} · {event['title']} · {STATES[event['state']]}" + (" · 大事件" if event['major'] else '')
        if role == Qt.ItemDataRole.ToolTipRole:
            return self.data(index) + '\n' + event['description']
        return None

    def replace(self, events):
        self.beginResetModel()
        self.events = events
        self.endResetModel()


class EventDelegate(QStyledItemDelegate):
    """Paint only visible nodes, with equal spacing rather than a date scale."""
    def sizeHint(self, option, index):
        return QSize(260, option.fontMetrics.height() * 5 + 34)

    def paint(self, painter, option, index):
        event = index.data(EventListModel.EventRole)
        if not event:
            return
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = option.rect
        palette = QApplication.palette()
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        accent = palette.color(QPalette.ColorRole.Highlight)
        text = palette.color(QPalette.ColorRole.Text)
        muted = palette.color(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text)
        x = rect.left() + 17
        mid = rect.top() + 25
        painter.setPen(QPen(palette.color(QPalette.ColorRole.Mid), 2))
        painter.drawLine(x, rect.top(), x, rect.bottom() + 1)
        card = QRectF(rect.adjusted(36, 5, -8, -5))
        painter.setBrush(palette.color(QPalette.ColorRole.AlternateBase))
        painter.setPen(QPen(accent if selected else palette.color(QPalette.ColorRole.Mid), 2 if selected else 1))
        painter.drawRoundedRect(card, 8, 8)
        radius = 7 if event['major'] else 5
        painter.setPen(QPen(muted if event['state'] == 'abandoned' else accent, 2))
        painter.setBrush(accent if event['state'] == 'written' else palette.color(QPalette.ColorRole.Base))
        painter.drawEllipse(QRectF(x-radius, mid-radius, radius*2, radius*2))
        if event['state'] == 'abandoned':
            painter.drawLine(x-4, mid-4, x+4, mid+4)
        left = int(card.left()) + 12
        width = max(1, int(card.width()) - 24)
        height = option.fontMetrics.height()
        painter.setPen(muted if event['state'] == 'abandoned' else text)
        chapter = '、'.join(event['chapter_ids']) or '未关联章节'
        rows = [event['time'] or '时间待定', event['title'],
                STATES[event['state']] + (' · 大事件' if event['major'] else '') + (' · '+event['storyline'] if event['storyline'] else ''),
                event['description'].replace('\n', ' ') or '暂无描述', chapter]
        for i, value in enumerate(rows):
            painter.drawText(left, rect.top()+18+option.fontMetrics.ascent()+i*height,
                option.fontMetrics.elidedText(value, Qt.TextElideMode.ElideRight, width))
        if option.state & QStyle.StateFlag.State_HasFocus:
            painter.setPen(QPen(accent, 1, Qt.PenStyle.DashLine))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(card.adjusted(3,3,-3,-3),5,5)
        painter.restore()


class EventToChapterDialog(QDialog):
    def __init__(self, store, event, parent=None):
        super().__init__(parent)
        self.store, self.event_data = store, event
        self.chapter_id = None
        self.setWindowTitle('加入章节素材')
        self.resize(480, 260)
        root = QVBoxLayout(self)
        title = QLabel(event['title']); title.setWordWrap(True); root.addWidget(title)
        form = QFormLayout()
        self.chapter = QComboBox()
        for path in store.project.list_chapters():
            self.chapter.addItem(store.project.load_chapter(path.stem).title, path.stem)
        self.chapter.setCurrentIndex(-1)
        self.chapter.setPlaceholderText('请选择目标章节')
        form.addRow('目标章节', self.chapter)
        self.use = combo(USES)
        self.use.setCurrentIndex(self.use.findData('develop'))
        form.addRow('素材用途', self.use)
        root.addLayout(form)
        hint = QLabel('确认后加入所选章节的素材并打开该章；不会自动生成或修改正文。')
        hint.setWordWrap(True); root.addWidget(hint)
        self.error = QLabel(); self.error.setWordWrap(True); root.addWidget(self.error)
        buttons(self, root, '加入并打开章节')

    def accept(self):
        chapter = self.chapter.currentData()
        if not chapter:
            self.error.setText('请选择目标章节。'); return
        try:
            selected = self.store.selection(chapter)
            selected[self.event_data['id']] = self.use.currentData()
            self.store.save_selection(chapter, selected)
        except (OSError, ValueError) as exc:
            self.error.setText(str(exc)); return
        self.chapter_id = chapter
        super().accept()


class TimelinePage(QWidget):
    changed = Signal()
    state_changed = Signal()
    chapter_requested = Signal(str)
    notes_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName('pageSurface')
        self.project = None
        self.store = None
        self._events = []
        self._selected_id = None
        self._detail_open = False
        self._refreshing = False
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)
        header = QHBoxLayout()
        title = QLabel('故事时间轴'); title.setObjectName('sectionTitle'); header.addWidget(title)
        header.addStretch()
        self.new_button = QPushButton('新建事件'); header.addWidget(self.new_button)
        self.notes_button = QPushButton('时间线笔记'); header.addWidget(self.notes_button)
        root.addLayout(header)
        hint = QLabel('按作者编排顺序展示 · 节点等距，不代表实际时间跨度')
        hint.setWordWrap(True); root.addWidget(hint)
        search_row = QHBoxLayout()
        self.search = QLineEdit(); self.search.setPlaceholderText('搜索事件、时间、人物或地点'); self.search.setAccessibleName('搜索时间轴')
        search_row.addWidget(self.search)
        reset = QPushButton('重置筛选'); search_row.addWidget(reset)
        reload_button = QPushButton('刷新'); search_row.addWidget(reload_button)
        root.addLayout(search_row)
        filters = QHBoxLayout()
        self.state_filter = combo(STATES, '全部状态'); self.state_filter.setAccessibleName('事件状态')
        self.story_filter = QComboBox(); self.story_filter.addItem('全部故事线',''); self.story_filter.setAccessibleName('故事线')
        self.chapter_filter = QComboBox(); self.chapter_filter.addItem('全部章节',''); self.chapter_filter.setAccessibleName('关联章节')
        for control in (self.state_filter, self.story_filter, self.chapter_filter):
            control.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
            control.setMinimumContentsLength(6)
            filters.addWidget(control,1)
        root.addLayout(filters)
        flags = QHBoxLayout()
        self.major_filter = QCheckBox('只看大事件'); self.deleted_filter = QCheckBox('已删除')
        flags.addWidget(self.major_filter); flags.addWidget(self.deleted_filter); flags.addStretch()
        self.count = QLabel(); flags.addWidget(self.count); root.addLayout(flags)
        self.message = QLabel(); self.message.setWordWrap(True); self.message.hide(); root.addWidget(self.message)
        self.body = QSplitter(Qt.Orientation.Horizontal)
        self.overview = QWidget(); overview_layout = QVBoxLayout(self.overview); overview_layout.setContentsMargins(0,0,0,0)
        self.model = EventListModel(self)
        self.list = QListView(); self.list.setModel(self.model); self.list.setItemDelegate(EventDelegate(self.list))
        self.list.setAccessibleName('故事事件时间轴')
        self.list.setUniformItemSizes(True)
        self.list.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        overview_layout.addWidget(self.list)
        details = QPushButton('查看事件详情'); details.clicked.connect(self.open_detail); overview_layout.addWidget(details)
        self.detail_panel = QWidget(); detail_layout = QVBoxLayout(self.detail_panel); detail_layout.setContentsMargins(6,0,0,0)
        self.back_button = QPushButton('返回时间轴'); self.back_button.clicked.connect(self.close_detail); detail_layout.addWidget(self.back_button)
        self.detail = QPlainTextEdit(); self.detail.setReadOnly(True); self.detail.setAccessibleName('事件完整详情'); detail_layout.addWidget(self.detail,1)
        row = QHBoxLayout()
        self.edit_button = QPushButton('编辑'); self.delete_button = QPushButton('删除')
        row.addWidget(self.edit_button); row.addWidget(self.delete_button); detail_layout.addLayout(row)
        row = QHBoxLayout()
        self.up_button = QPushButton('上移'); self.down_button = QPushButton('下移'); self.move_button = QPushButton('移到…')
        for button in (self.up_button,self.down_button,self.move_button): row.addWidget(button)
        detail_layout.addLayout(row)
        self.chapter_picker = QComboBox(); self.chapter_picker.setAccessibleName('事件关联章节')
        self.chapter_picker.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.chapter_picker.setMinimumContentsLength(6)
        detail_layout.addWidget(self.chapter_picker)
        self.open_chapter_button = QPushButton('打开关联章节'); detail_layout.addWidget(self.open_chapter_button)
        self.material_button = QPushButton('加入章节素材…'); detail_layout.addWidget(self.material_button)
        self.body.addWidget(self.overview); self.body.addWidget(self.detail_panel)
        self.body.setStretchFactor(0,3); self.body.setStretchFactor(1,2)
        root.addWidget(self.body,1)
        self.new_button.clicked.connect(self.new_event)
        self.notes_button.clicked.connect(self.notes_requested)
        self.edit_button.clicked.connect(self.edit_event)
        self.delete_button.clicked.connect(self.toggle_deleted)
        self.up_button.clicked.connect(lambda: self.move(-1))
        self.down_button.clicked.connect(lambda: self.move(1))
        self.move_button.clicked.connect(self.move_to)
        self.material_button.clicked.connect(self.add_material)
        self.open_chapter_button.clicked.connect(lambda: self.chapter_requested.emit(self.chapter_picker.currentData()) if self.chapter_picker.currentData() else None)
        self.list.selectionModel().currentChanged.connect(self.select_event)
        self.list.activated.connect(lambda _: self.open_detail())
        self.list.verticalScrollBar().valueChanged.connect(self.state_changed)
        for control in (self.state_filter,self.story_filter,self.chapter_filter): control.currentIndexChanged.connect(self.apply_filters)
        self.search.textChanged.connect(self.apply_filters)
        self.major_filter.toggled.connect(self.apply_filters); self.deleted_filter.toggled.connect(self.reload)
        reset.clicked.connect(self.reset_filters); reload_button.clicked.connect(self.reload)
        self.show_project(None)

    def resizeEvent(self,event):
        super().resizeEvent(event)
        self.adapt()

    def adapt(self):
        narrow = self.width() < 800
        self.overview.setVisible(not narrow or not self._detail_open)
        self.detail_panel.setVisible(not narrow or self._detail_open)
        self.back_button.setVisible(narrow)

    def open_detail(self):
        if self.current():
            self._detail_open=True; self.adapt(); self.detail.setFocus()

    def close_detail(self):
        self._detail_open=False; self.adapt(); self.list.setFocus()

    def current(self):
        return self.list.currentIndex().data(EventListModel.EventRole)

    def view_state(self):
        return dict(query=self.search.text()[:200], status=self.state_filter.currentData(), storyline=self.story_filter.currentData(),
            chapter=self.chapter_filter.currentData(), major=self.major_filter.isChecked(), deleted=self.deleted_filter.isChecked(),
            selected=self._selected_id, scroll=self.list.verticalScrollBar().value())

    def restore_state(self,state):
        if not isinstance(state,dict): return
        self._refreshing=True
        self.deleted_filter.setChecked(state.get('deleted') is True)
        self._refreshing=False
        self.reload()  # Build the story-line choices for the requested collection.
        self._refreshing=True
        self.search.setText(str(state.get('query') or '')[:200])
        for key,control in (('status',self.state_filter),('storyline',self.story_filter),('chapter',self.chapter_filter)):
            control.setCurrentIndex(max(0,control.findData(state.get(key,''))))
        self.major_filter.setChecked(state.get('major') is True)
        self._selected_id=state.get('selected') if isinstance(state.get('selected'),str) else None
        self._refreshing=False
        self.apply_filters()
        scroll=state.get('scroll',0)
        self.list.doItemsLayout()
        if type(scroll) is int: self.list.verticalScrollBar().setValue(max(0,min(2000000000,scroll)))

    def show_project(self,project):
        if self.project is not None and project is not None and self.project.root == project.root:
            self.reload(); return
        self.project=project; self.store=None; self._events=[]; self._selected_id=None
        self._detail_open=False
        self.reset_filters()
        self.reload()
        self.adapt()

    def reset_filters(self):
        self._refreshing=True
        self.search.clear()
        for control in (self.state_filter,self.story_filter,self.chapter_filter): control.setCurrentIndex(0)
        self.major_filter.setChecked(False); self.deleted_filter.setChecked(False)
        self._refreshing=False
        self.reload()

    def reload(self,*_):
        if self._refreshing: return
        self.message.hide()
        if self.project is None:
            self.store=None; self._events=[]
        else:
            try:
                self.store=TimelineStore(self.project)
                self._events=self.store.events(deleted=self.deleted_filter.isChecked())
            except (OSError,ValueError) as exc:
                self.store=None; self._events=[]; self.message.setText(str(exc)); self.message.show()
        self._refreshing=True
        for control,entries in ((self.story_filter,[(v,v) for v in dict.fromkeys(e['storyline'] for e in self._events) if v]),
                (self.chapter_filter,[(self.project.load_chapter(p.stem).title,p.stem) for p in self.project.list_chapters()] if self.project else [])):
            selected=control.currentData()
            while control.count()>1: control.removeItem(1)
            for title,value in entries: control.addItem(title,value)
            control.setCurrentIndex(max(0,control.findData(selected)))
        self._refreshing=False
        self.new_button.setEnabled(self.store is not None)
        self.notes_button.setEnabled(self.project is not None and (self.project.canon_dir/'timeline.md').is_file())
        self.apply_filters()

    def apply_filters(self,*_):
        if self._refreshing: return
        scroll=self.list.verticalScrollBar().value()
        query=self.search.text().strip().casefold()
        events=[e for e in self._events if
            (not query or query in ' '.join(str(e[k]) for k in ('title','description','time','characters','location','storyline')).casefold()) and
            (not self.state_filter.currentData() or e['state']==self.state_filter.currentData()) and
            (not self.story_filter.currentData() or e['storyline']==self.story_filter.currentData()) and
            (not self.chapter_filter.currentData() or self.chapter_filter.currentData() in e['chapter_ids']) and
            (not self.major_filter.isChecked() or e['major'])]
        selected_id=self._selected_id
        self._refreshing=True
        self.model.replace(events)
        row=next((i for i,e in enumerate(events) if e['id']==selected_id),0)
        self.list.setCurrentIndex(self.model.index(row) if events else QModelIndex())
        self._refreshing=False
        self.list.verticalScrollBar().setValue(scroll)
        self.count.setText(f'{len(events)} / {len(self._events)} 个事件')
        if not events and self.store is not None:
            self.message.setText('没有匹配的事件，请调整筛选。' if self._events else '暂无事件，点击“新建事件”开始梳理故事。')
            self.message.show()
        elif self.store is not None:
            self.message.hide()
        self.select_event()
        self.state_changed.emit()

    def select_event(self,*_):
        if self._refreshing: return
        event=self.current()
        if event: self._selected_id=event['id']
        self.chapter_picker.clear()
        existing={p.stem:p for p in self.project.list_chapters()} if self.project else {}
        if event:
            self.detail.setPlainText('\n\n'.join((event['title'], f"{event['time'] or '时间待定'} · {STATES[event['state']]}",
                event['description'] or '暂无描述',f"人物：{event['characters'] or '未填写'}\n地点：{event['location'] or '未填写'}\n故事线：{event['storyline'] or '未填写'}",
                '关联章节：'+('、'.join(event['chapter_ids']) or '未关联'))))
            for chapter in event['chapter_ids']:
                if chapter in existing: self.chapter_picker.addItem(self.project.load_chapter(chapter).title,chapter)
        else: self.detail.setPlainText('选择一个事件查看详情。')
        active=bool(event) and not event['deleted']
        filtered=bool(self.search.text() or self.state_filter.currentData() or self.story_filter.currentData() or self.chapter_filter.currentData() or self.major_filter.isChecked())
        self.edit_button.setEnabled(active)
        self.delete_button.setEnabled(bool(event))
        self.delete_button.setText('恢复' if event and event['deleted'] else '删除')
        row=self.list.currentIndex().row()
        self.up_button.setEnabled(active and not filtered and row>0)
        self.down_button.setEnabled(active and not filtered and row<len(self.model.events)-1)
        self.move_button.setEnabled(active and not filtered and len(self.model.events)>1)
        for button in (self.up_button,self.down_button,self.move_button): button.setToolTip('重置筛选后可调整全局顺序' if filtered else '调整作者编排顺序，不修改时间标签')
        self.open_chapter_button.setEnabled(bool(self.chapter_picker.count()))
        self.material_button.setEnabled(active and event['state']!='abandoned' and bool(existing))
        self.state_changed.emit()

    def _mutate(self,operation):
        try: operation()
        except (OSError,ValueError) as exc:
            self.message.setText(str(exc)); self.message.show(); return False
        self.reload(); self.changed.emit(); return True

    def new_event(self):
        self.edit_event(new=True)

    def edit_event(self,new=False):
        if self.store is None or (not new and not self.current()): return
        dialog=EventEditorDialog(self.store,None if new else self.current(),self)
        if dialog.exec()==QDialog.DialogCode.Accepted:
            self._selected_id=dialog.saved_id
            if new:
                self.reset_filters()
            else:
                self.reload()
            self.changed.emit()
            self.list.scrollTo(self.list.currentIndex())

    def toggle_deleted(self):
        event=self.current()
        if event and self.store:
            self._mutate(lambda:self.store.set_deleted(event['id'],not event['deleted']))

    def move(self,offset):
        event=self.current()
        allowed=self.up_button.isEnabled() if offset<0 else self.down_button.isEnabled()
        if event and allowed: self._mutate(lambda:self.store.move(event['id'],offset))

    def move_to(self):
        event=self.current()
        if not event or not self.move_button.isEnabled(): return
        others=[e for e in self.model.events if e['id']!=event['id']]
        labels=[f"{i+1}. {e['title']}" for i,e in enumerate(others)]
        label,ok=QInputDialog.getItem(self,'调整事件顺序','选择目标事件',labels,0,False)
        if not ok: return
        position,ok=QInputDialog.getItem(self,'调整事件顺序','移动到目标事件', ['之前','之后'],0,False)
        if ok: self._mutate(lambda:self.store.move_to(event['id'],others[labels.index(label)]['id'],after=position=='之后'))

    def add_material(self):
        event=self.current()
        if not event or not self.material_button.isEnabled(): return
        dialog=EventToChapterDialog(self.store,event,self)
        if dialog.exec()==QDialog.DialogCode.Accepted:
            self.changed.emit()
            self.chapter_requested.emit(dialog.chapter_id)
