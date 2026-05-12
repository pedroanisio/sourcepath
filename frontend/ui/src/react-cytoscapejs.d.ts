declare module "react-cytoscapejs" {
  import type { Core, ElementDefinition, LayoutOptions, Stylesheet } from "cytoscape";
  import type { CSSProperties } from "react";

  export interface CytoscapeComponentProps {
    elements: ElementDefinition[];
    style?: CSSProperties;
    className?: string;
    layout?: LayoutOptions | { name: string; [k: string]: unknown };
    stylesheet?: Stylesheet[] | unknown[];
    cy?: (cy: Core) => void;
    zoom?: number;
    pan?: { x: number; y: number };
    minZoom?: number;
    maxZoom?: number;
    zoomingEnabled?: boolean;
    userZoomingEnabled?: boolean;
    panningEnabled?: boolean;
    userPanningEnabled?: boolean;
    boxSelectionEnabled?: boolean;
    autoungrabify?: boolean;
    autounselectify?: boolean;
    headless?: boolean;
    styleEnabled?: boolean;
    hideEdgesOnViewport?: boolean;
    hideLabelsOnViewport?: boolean;
    textureOnViewport?: boolean;
    motionBlur?: boolean;
    motionBlurOpacity?: number;
    wheelSensitivity?: number;
  }

  const CytoscapeComponent: React.FC<CytoscapeComponentProps>;
  export default CytoscapeComponent;
}
