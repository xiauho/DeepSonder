"""UI-independent application services shared by desktop frontends."""

from .document_service import (
    DocumentMutation,
    DocumentNotFoundError,
    DocumentPathError,
    DocumentRevisionConflict,
    DocumentService,
    DocumentServiceError,
    DocumentSnapshot,
)
from .project_service import OpenedProject, ProjectService, RecentProjects
from .project_content_service import (
    ProjectContentService,
    ProjectContentSnapshot,
    ProjectDocumentItem,
    TrashItem,
    TrashSnapshot,
)
from .ai_task_service import AITaskService, AITaskRecord
from .relationship_graph_service import (
    GraphEdge,
    GraphEvidence,
    GraphNode,
    GraphWarning,
    RelationshipGraphService,
    RelationshipGraphSnapshot,
)
from .preferences_service import PreferencesService, PreferencesSnapshot
from .manuscript_import_service import (
    ImportChapterCandidate,
    ImportWarning,
    ManuscriptImportError,
    ManuscriptImportPlan,
    ManuscriptImportService,
)
from .project_v2_service import ProjectV2Service
from .reconstruction_service import KnowledgeSnapshot, ReconstructionCancelled, ReconstructionError, ReconstructionService, ReconstructionSnapshot
from .structured_extraction_service import StructuredExtractionError, StructuredExtractionResult, StructuredExtractionService
from .reconstruction_evaluation_service import EvaluationReport, ReconstructionEvaluationError, ReconstructionEvaluationService
from .reconstruction_task_service import ReconstructionTaskRecord, ReconstructionTaskService
from .document_v2_service import DocumentV2Service, ManuscriptItem, ManuscriptSnapshot

__all__ = [
    "DocumentMutation",
    "AITaskRecord",
    "AITaskService",
    "DocumentNotFoundError",
    "DocumentPathError",
    "DocumentRevisionConflict",
    "DocumentService",
    "DocumentServiceError",
    "DocumentSnapshot",
    "DocumentV2Service",
    "GraphEdge",
    "GraphEvidence",
    "GraphNode",
    "GraphWarning",
    "ImportChapterCandidate",
    "ImportWarning",
    "ManuscriptImportError",
    "ManuscriptImportPlan",
    "ManuscriptImportService",
    "ManuscriptItem",
    "ManuscriptSnapshot",
    "OpenedProject",
    "PreferencesService",
    "PreferencesSnapshot",
    "ProjectContentService",
    "ProjectContentSnapshot",
    "ProjectDocumentItem",
    "ProjectService",
    "ProjectV2Service",
    "ReconstructionError",
    "KnowledgeSnapshot",
    "ReconstructionCancelled",
    "ReconstructionService",
    "StructuredExtractionError",
    "StructuredExtractionResult",
    "StructuredExtractionService",
    "EvaluationReport",
    "ReconstructionEvaluationError",
    "ReconstructionEvaluationService",
    "ReconstructionSnapshot",
    "ReconstructionTaskRecord",
    "ReconstructionTaskService",
    "RecentProjects",
    "RelationshipGraphService",
    "RelationshipGraphSnapshot",
    "TrashItem",
    "TrashSnapshot",
]
