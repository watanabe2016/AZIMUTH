"""统一课程快照；绘制与讲解使用同一份已验证结果。"""
from dataclasses import dataclass, field
from teaching_math import Point


@dataclass(frozen=True)
class VisualObject:
    id: str
    kind: str
    points: tuple = ()
    label: str = ''
    role: str = 'task'
    value: float = 0


@dataclass(frozen=True)
class LessonStep:
    id: str
    title: str
    explanation: str
    formula: str = ''
    substitution: str = ''
    result: str = ''
    objects: tuple = ()
    highlights: tuple = ()
    duration: int = 1400
    motion: str = 'reveal'
    known_conditions: tuple = ()
    warnings: tuple = ()
    interaction: str = ''
    next_condition: str = ''


@dataclass(frozen=True)
class Question:
    prompt: str
    answer: float
    unit: str = ''
    tolerance: float = .01
    explanation: str = ''


@dataclass(frozen=True)
class Lesson:
    id: str
    title: str
    steps: tuple
    parameters: dict = field(default_factory=dict)
    questions: tuple = ()
    status: str = 'unique'
    warnings: tuple = ()
    source: str = '教学示例'
    version: int = 2


@dataclass
class LessonSession:
    lesson_id: str = ''
    revision: int = 0
    step: int = 0
    progress: float = 0
    playing: bool = False
    speed: float = 1
    mode: str = 'teach'
    answers: dict = field(default_factory=dict)
    viewport: dict = field(default_factory=dict)
