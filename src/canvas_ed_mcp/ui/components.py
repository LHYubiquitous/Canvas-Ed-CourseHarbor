"""Small reusable PySide6 presentation components."""

from __future__ import annotations

from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout


class CollapsibleNotice(QFrame):
    """Compact summary with optional details and a dismiss action."""

    def __init__(
        self,
        *,
        summary: str = "",
        text: str = "",
        tone: str = "info",
        dismissible: bool = True,
        expanded: bool = False,
        accessibility_subject: str = "notice",
    ) -> None:
        super().__init__()
        self._accessibility_subject = accessibility_subject
        self.setObjectName("collapsible_notice")
        self.setProperty("tone", tone)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 6, 8, 6)
        layout.setSpacing(4)
        header = QHBoxLayout()
        self.summary_label = QLabel(summary)
        self.summary_label.setObjectName("notice_summary")
        self.toggle_button = QPushButton()
        self.toggle_button.setObjectName("notice_toggle_button")
        self.toggle_button.clicked.connect(self.toggle_details)
        self.close_button = QPushButton("×")
        self.close_button.setObjectName("notice_close_button")
        self.close_button.setAccessibleName(f"Hide {accessibility_subject}")
        self.close_button.setFixedWidth(30)
        self.close_button.clicked.connect(self.hide)
        self.close_button.setVisible(dismissible)
        header.addWidget(self.summary_label, 1)
        header.addWidget(self.toggle_button)
        header.addWidget(self.close_button)
        self.details_label = QLabel(text)
        self.details_label.setObjectName("notice_details")
        self.details_label.setWordWrap(True)
        layout.addLayout(header)
        layout.addWidget(self.details_label)
        self.setExpanded(expanded)

    def text(self) -> str:
        return self.details_label.text()

    def setText(self, text: str) -> None:
        self.details_label.setText(text)

    def setSummary(self, summary: str) -> None:
        self.summary_label.setText(summary)

    def setTone(self, tone: str) -> None:
        self.setProperty("tone", tone)
        self.style().unpolish(self)
        self.style().polish(self)

    def setExpanded(self, expanded: bool) -> None:
        self.details_label.setVisible(expanded)
        self.toggle_button.setText("Hide details" if expanded else "Show details")
        self.toggle_button.setAccessibleName(
            f"Hide {self._accessibility_subject} details"
            if expanded
            else f"Show {self._accessibility_subject} details"
        )

    def toggle_details(self) -> None:
        self.setExpanded(self.details_label.isHidden())


__all__ = ["CollapsibleNotice"]
