// PROPOSED output shapes for GET /map?format=geojson and GET /hotspots (NEEDS-LEAD).
// Not in core/models.py yet; once approved they move into types.ts next to the other contracts.

import type { IsoDateTime, Visibility } from "./types";

/** GeoJSON order: [min_lon, min_lat, max_lon, max_lat]. */
export type GeoJsonBBox = [number, number, number, number];

export interface CellFeature {
  type: "Feature";
  geometry: { type: "Polygon"; coordinates: number[][][] }; // [lon, lat] rings
  properties: { value?: number; score?: number; row: number; col: number };
}

export interface CellCollection {
  type: "FeatureCollection";
  bbox?: GeoJsonBBox;
  features: CellFeature[];
}

/** One FeatureCollection per visible layer (engine/grid.Grid.to_geojson), keyed by layer_id; "score" is the combined map. */
export interface MapResponse {
  app_id: string;
  t: IsoDateTime;
  cell_m: number;
  bbox: GeoJsonBBox;
  layers: Record<string, CellCollection>;
}

export interface Hotspot {
  id: string;
  lat: number;
  lon: number;
  radius_m: number;
  score: number;
  contributing_layers?: string[];
  visibility?: Visibility;
}

export interface HotspotsResponse {
  app_id: string;
  t: IsoDateTime;
  hotspots: Hotspot[];
}

export function cellValue(f: CellFeature): number {
  return f.properties.value ?? f.properties.score ?? 0;
}
