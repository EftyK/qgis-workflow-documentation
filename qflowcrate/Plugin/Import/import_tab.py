# -*- coding: utf-8 -*-
"""
Import Tab Widget - Widget for importing and visualizing RO-Crate workflows
"""

import json
import zipfile
from pathlib import Path

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from qflowcrate.Plugin.Graph.connection_arrow import ConnectionArrow
from qflowcrate.Plugin.Graph.graph_view import GraphView
from qflowcrate.Plugin.Graph.layer_node import LayerNode
from qflowcrate.Plugin.Graph.process_node import ProcessNode
from qflowcrate.Plugin.utility import get_logger


class ImportedLayer:
    """Mock layer object to satisfy LayerNode requirements for display only"""
    def __init__(self, layer_id, name, layer_type="Unknown"):
        self.id = layer_id
        self.name = name
        self.type = layer_type
        self.visible = True
        self.external = False


class ImportedProcess:
    """Mock process object to satisfy ProcessNode requirements for display only"""
    def __init__(self, process_id, name, algorithm_id="Unknown"):
        self.id = process_id
        self.name = name
        self.algorithm_id = algorithm_id
        
    def set_input(self, ids):
        pass
        
    def set_result(self, ids):
        pass


class ImportTab(QWidget):
    """Widget for importing and visualizing RO-Crate workflows"""

    # ============================================================================
    # INITIALIZATION
    # ============================================================================

    def __init__(self, parent=None):
        """Initialize the Import tab widget.

        :param parent: Parent widget
        :type parent: QWidget
        """
        super().__init__(parent)
        self.logger = get_logger("ImportTab")
        self.nodes_map = {}  # Map of @id -> Node instance

        self.setup_ui()

    # ============================================================================
    # UI SETUP
    # ============================================================================

    def setup_ui(self):
        """Setup the user interface programmatically"""
        main_layout = QVBoxLayout()
        main_layout.setContentsMargins(10, 10, 10, 10)
        main_layout.setSpacing(12)

        # Instruction label
        instruction_label = QLabel(
            "Select an exported RO-Crate (.zip) file to visualize its workflow graph. "
            "This view is read-only and will not load data into your QGIS project."
        )
        instruction_label.setWordWrap(True)
        main_layout.addWidget(instruction_label)

        # File selection layout
        file_layout = QHBoxLayout()
        file_layout.setSpacing(8)

        self.file_path_lineedit = QLineEdit()
        self.file_path_lineedit.setPlaceholderText("Select RO-Crate .zip file...")
        self.file_path_lineedit.setReadOnly(True)
        file_layout.addWidget(self.file_path_lineedit)

        self.browse_btn = QPushButton("Browse...")
        self.browse_btn.clicked.connect(self.browse_file)
        file_layout.addWidget(self.browse_btn)

        self.clear_btn = QPushButton("Clear Graph")
        self.clear_btn.clicked.connect(self.clear_graph)
        file_layout.addWidget(self.clear_btn)

        main_layout.addLayout(file_layout)

        # Graph view (reusing the custom GraphView)
        self.graph_view = GraphView()
        self.graph_view.setMinimumHeight(400)
        
        # Disable connection mode features to ensure it remains read-only
        self.graph_view.setAcceptDrops(False)
        main_layout.addWidget(self.graph_view)

        self.setLayout(main_layout)

    # ============================================================================
    # FILE HANDLING
    # ============================================================================

    def browse_file(self):
        """Open file dialog to select RO-Crate zip"""
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Select RO-Crate Archive",
            "",
            "ZIP Files (*.zip);;All Files (*)"
        )

        if file_path:
            self.file_path_lineedit.setText(file_path)
            self.load_graph_from_crate(file_path)

    def clear_graph(self):
        """Clear all items from graph"""
        self.graph_view.scene.clear()
        self.nodes_map.clear()
        self.file_path_lineedit.clear()

    # ============================================================================
    # GRAPH PARSING & RENDERING
    # ============================================================================

    def load_graph_from_crate(self, zip_path):
        """Parse RO-Crate zip and build the visual graph.

        :param zip_path: Path to the .zip file
        :type zip_path: str
        """
        self.graph_view.scene.clear()
        self.nodes_map.clear()

        try:
            with zipfile.ZipFile(zip_path, 'r') as zf:
                if 'ro-crate-metadata.json' not in zf.namelist():
                    raise ValueError("ro-crate-metadata.json not found in the archive.")
                
                with zf.open('ro-crate-metadata.json') as f:
                    metadata = json.load(f)

            graph_data = metadata.get("@graph", [])
            
            # Map all entities by their @id for easy lookup
            entity_map = {item.get("@id"): item for item in graph_data}
            
            # 1. Identify Processing Steps (CreateAction) and Data Entities
            actions = []
            for item in graph_data:
                types = item.get("@type", [])
                if isinstance(types, str):
                    types = [types]
                
                if "CreateAction" in types:
                    actions.append(item)

            # 2. Reconstruct Nodes
            connections_to_make = []  # Store tuples of (source_id, target_id)

            for action in actions:
                action_id = action.get("@id")
                action_name = action.get("name", "Processing Step")
                
                # Fetch instrument/algorithm info if available
                instrument_ref = action.get("instrument", {}).get("@id")
                algo_id = "Unknown Algorithm"
                if instrument_ref and instrument_ref in entity_map:
                    algo_id = entity_map[instrument_ref].get("name", instrument_ref)

                # Create Process Node
                process_obj = ImportedProcess(action_id, action_name, algo_id)
                p_node = ProcessNode(process_obj)
                self._disable_interactions(p_node)
                self.nodes_map[action_id] = p_node
                self.graph_view.scene.addItem(p_node)

                # Process Inputs (Data -> Process)
                inputs = action.get("object", [])
                if isinstance(inputs, dict):
                    inputs = [inputs]
                
                for input_ref in inputs:
                    in_id = input_ref.get("@id")
                    if in_id:
                        self._ensure_layer_node_exists(in_id, entity_map)
                        connections_to_make.append((in_id, action_id))

                # Process Outputs (Process -> Data)
                outputs = action.get("result", [])
                if isinstance(outputs, dict):
                    outputs = [outputs]
                
                for output_ref in outputs:
                    out_id = output_ref.get("@id")
                    if out_id:
                        self._ensure_layer_node_exists(out_id, entity_map)
                        connections_to_make.append((action_id, out_id))

            # 3. Draw Connections
            for source_id, target_id in connections_to_make:
                source_node = self.nodes_map.get(source_id)
                target_node = self.nodes_map.get(target_id)
                
                if source_node and target_node:
                    arrow = ConnectionArrow(source_node, target_node)
                    self.graph_view.scene.addItem(arrow)

            # 4. Apply auto-layout to make the graph readable
            self._apply_auto_layout(connections_to_make)

        except Exception as e:
            self.logger.error(f"Failed to load RO-Crate: {e}")
            QMessageBox.critical(self, "Import Error", f"Failed to parse RO-Crate:\n{str(e)}")
            self.clear_graph()

    def _ensure_layer_node_exists(self, layer_id, entity_map):
        """Create a LayerNode if it hasn't been created yet."""
        if layer_id not in self.nodes_map:
            entity = entity_map.get(layer_id, {})
            name = entity.get("name", Path(layer_id).stem if not layer_id.startswith('#') else layer_id)
            
            layer_type = "Unknown"
            # Attempt to extract type from custom context or additionalType
            additional_type = entity.get("additionalType", "")
            if additional_type:
                layer_type = additional_type if isinstance(additional_type, str) else additional_type[0]
            
            layer_obj = ImportedLayer(layer_id, name, layer_type)
            l_node = LayerNode(layer_obj)
            self._disable_interactions(l_node)
            self.nodes_map[layer_id] = l_node
            self.graph_view.scene.addItem(l_node)

    def _disable_interactions(self, node):
        """Override context menu to prevent crashes from mock objects."""
        node.contextMenuEvent = lambda event: None
        node.setToolTip(node.toolTip() + "\n(Read-Only)")

    # ============================================================================
    # GRAPH LAYOUT ENGINE
    # ============================================================================

    def _apply_auto_layout(self, edges):
        """A simple left-to-right topological layout algorithm."""
        if not self.nodes_map:
            return

        # Track incoming/outgoing edges to find root nodes
        in_degrees = {node_id: 0 for node_id in self.nodes_map.keys()}
        out_edges = {node_id: [] for node_id in self.nodes_map.keys()}
        
        for source, target in edges:
            in_degrees[target] = in_degrees.get(target, 0) + 1
            out_edges[source].append(target)

        # Find nodes with no incoming edges (roots)
        queue = [node_id for node_id, degree in in_degrees.items() if degree == 0]
        
        # If there's a cycle or isolated graph structure, just grab arbitrary nodes
        if not queue and self.nodes_map:
            queue = [list(self.nodes_map.keys())[0]]

        levels = {}
        while queue:
            current_id = queue.pop(0)
            current_level = levels.get(current_id, 0)
            
            for neighbor in out_edges.get(current_id, []):
                # Push neighbor to the next level
                levels[neighbor] = max(levels.get(neighbor, 0), current_level + 1)
                in_degrees[neighbor] -= 1
                if in_degrees[neighbor] <= 0:
                    queue.append(neighbor)

        # Handle any floating/isolated nodes that didn't get a level
        for node_id in self.nodes_map:
            if node_id not in levels:
                levels[node_id] = 0

        # Group nodes by their calculated horizontal level
        level_groups = {}
        for node_id, lvl in levels.items():
            level_groups.setdefault(lvl, []).append(node_id)

        # Apply positional coordinates
        horizontal_spacing = 250
        vertical_spacing = 150
        
        for lvl, node_ids in level_groups.items():
            x_pos = 50 + (lvl * horizontal_spacing)
            
            # Center the vertical stack
            total_height = len(node_ids) * vertical_spacing
            start_y = (self.graph_view.scene.sceneRect().height() - total_height) / 2
            
            for index, node_id in enumerate(node_ids):
                y_pos = max(50, start_y + (index * vertical_spacing))
                self.nodes_map[node_id].setPos(x_pos, y_pos)

        # Force arrows to redraw to snap to their new node coordinates
        for node in self.nodes_map.values():
            if isinstance(node, LayerNode):
                for arrow in node.connections + node.input_arrows:
                    arrow.update_position()
            elif isinstance(node, ProcessNode):
                for arrow in node.input_arrows + node.output_arrows:
                    arrow.update_position()