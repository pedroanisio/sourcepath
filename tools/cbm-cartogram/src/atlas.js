(function startCartogram() {
  "use strict";

  if (!window.d3 || !window.ATLAS_DATA || !window.AtlasModel) {
    document.body.innerHTML = "<pre style='padding:2rem;color:#fff'>The atlas could not start because D3, atlas data, or the projection model is missing.</pre>";
    return;
  }

  const d3 = window.d3;
  const model = window.AtlasModel.derive(window.ATLAS_DATA);
  const data = model.data;

  const canvas = document.querySelector("#atlas-canvas");
  const interactionLayer = d3.select("#interaction-layer");
  const context = canvas.getContext("2d", { alpha: true, desynchronized: true });
  const tooltip = document.querySelector("#tooltip");
  const detailsPanel = document.querySelector("#details-panel");
  const searchInput = document.querySelector("#search-input");
  const searchResults = document.querySelector("#search-results");
  const zoomReadout = document.querySelector("#zoom-readout");

  const THEME_STORE = "cartogram.theme";
  const MODE_STORE = "cartogram.mode";
  const readStored = (key, fallback) => { try { return window.localStorage.getItem(key) || fallback; } catch (_) { return fallback; } };
  const writeStored = (key, value) => { try { window.localStorage.setItem(key, value); } catch (_) { /* storage unavailable */ } };

  let activeTheme = readStored(THEME_STORE, CartogramThemes.DEFAULT_ID);
  let activeMode = readStored(MODE_STORE, "dark");
  if (!CartogramThemes.has(activeTheme)) activeTheme = CartogramThemes.DEFAULT_ID;
  if (!CartogramThemes.MODES.includes(activeMode)) activeMode = "dark";

  // Single source of truth for color is themes.js. applyTheme() writes the CSS
  // custom properties (chrome) and mutates COLORS in place (canvas), so every
  // COLORS.* reference stays valid and the CSS-vs-JS palette duplication is gone.
  const COLORS = {};
  function applyTheme(themeId, mode, redraw) {
    if (!CartogramThemes.has(themeId)) themeId = CartogramThemes.DEFAULT_ID;
    if (!CartogramThemes.MODES.includes(mode)) mode = "dark";
    activeTheme = themeId;
    activeMode = mode;
    const vars = CartogramThemes.cssVars(themeId, mode);
    const rootStyle = document.documentElement.style;
    for (const key of Object.keys(vars)) rootStyle.setProperty(key, vars[key]);
    document.documentElement.style.colorScheme = mode;
    Object.assign(COLORS, CartogramThemes.canvasColors(themeId, mode));
    writeStored(THEME_STORE, themeId);
    writeStored(MODE_STORE, mode);
    if (redraw !== false && typeof scheduleDraw === "function") scheduleDraw();
  }
  applyTheme(activeTheme, activeMode, false);

  const REGION_COLORS = [
    "#4b7d92", "#715e92", "#8a5c68", "#557c66", "#766747", "#4d678d", "#7c5b7f", "#5d7276",
  ];

  const state = {
    mode: "combined",
    detail: "adaptive",
    layers: {
      regions: true,
      directories: true,
      nodes: true,
      primary: true,
      secondary: true,
      external: true,
      test: true,
      collections: true,
      chunks: true,
      labels: true,
      motion: !window.matchMedia("(prefers-reduced-motion: reduce)").matches,
    },
    transform: d3.zoomIdentity,
    selected: null,
    hovered: null,
    searchIndex: -1,
    width: 0,
    height: 0,
    dpr: 1,
    drawQueued: false,
    lastMotionFrame: 0,
    animationRunning: false,
    pointerDown: null,
  };

  const layout = buildLayout();
  const searchRecords = buildSearchRecords();
  const fileQuadtree = d3.quadtree(model.files, (file) => file.x, (file) => file.y);
  const chunkQuadtree = d3.quadtree(layout.chunkNodes, (chunk) => chunk.x, (chunk) => chunk.y);
  const externalQuadtree = d3.quadtree(model.externals, (item) => item.x, (item) => item.y);
  const suiteQuadtree = d3.quadtree(model.suites, (suite) => suite.x, (suite) => suite.y);
  const junctionQuadtree = d3.quadtree(layout.junctions, (junction) => junction.x, (junction) => junction.y);

  configureInterface();
  configureZoom();
  resize();
  setMode("combined");
  fitAtlas(false);
  scheduleDraw();

  function buildLayout() {
    const worldSize = 2380;
    const rootHierarchy = d3
      .hierarchy(model.directoryTree)
      .sum((node) => node.kind === "file" ? node.value : 0)
      .sort((a, b) => b.value - a.value || String(a.data.path).localeCompare(String(b.data.path), "en"));

    const packed = d3.pack().size([worldSize, worldSize]).padding(3.1)(rootHierarchy);
    const center = worldSize / 2;
    const directories = [];
    const regions = [];
    const regionById = new Map();

    packed.each((node) => {
      const x = node.x - center;
      const y = node.y - center;
      if (node.data.kind === "file") {
        const file = model.files[node.data.file];
        file.x = x;
        file.y = y;
        file.r = Math.max(1.7, node.r);
        file.layoutDepth = node.depth;
        return;
      }
      if (node.depth === 0) return;
      const directory = {
        id: node.data.path,
        name: node.data.name,
        path: node.data.path,
        x,
        y,
        r: node.r,
        depth: node.depth,
        count: node.leaves().length,
      };
      directories.push(directory);
      if (node.depth === 1) {
        const id = node.data.path;
        const regionData = model.regionMap.get(id) ?? { id, name: id, files: node.leaves().map((leaf) => leaf.data.file), production: 0, tests: 0, gates: 0 };
        const region = Object.assign(regionData, directory, {
          color: REGION_COLORS[regions.length % REGION_COLORS.length],
          kind: "region",
        });
        regions.push(region);
        regionById.set(id, region);
      }
    });

    for (const region of model.regions) {
      const positioned = regionById.get(region.id);
      if (positioned) Object.assign(region, positioned);
    }

    const externalRingStart = packed.r + 80;
    const externalSorted = [...model.externals].sort((a, b) => b.consumers.length - a.consumers.length || a.name.localeCompare(b.name));
    const bands = Math.max(3, Math.ceil(externalSorted.length / 72));
    const bandCounts = Array.from({ length: bands }, () => 0);
    externalSorted.forEach((external, rank) => {
      const band = rank % bands;
      const indexInBand = bandCounts[band]++;
      const totalInBand = Math.ceil((externalSorted.length - band) / bands);
      const angle = -Math.PI / 2 + (2 * Math.PI * indexInBand) / Math.max(1, totalInBand) + band * 0.027;
      const radius = externalRingStart + band * 42 + 7 * Math.sin((external.seed % 31) * 0.3);
      external.x = radius * Math.cos(angle);
      external.y = radius * Math.sin(angle);
      external.r = 3.7 + Math.min(9, Math.sqrt(external.consumers.length) * 1.45);
      external.rank = rank;
    });

    const suiteCircles = model.suites.map((suite) => ({
      ...suite,
      r: 2.8 + Math.min(16, Math.sqrt(suite.members.length) * 1.15),
    }));
    d3.packSiblings(suiteCircles);
    const suiteEnclosure = d3.packEnclose(suiteCircles);
    const testsRegion = regionById.get("tests") ?? regions.find((region) => region.tests > 0) ?? regions[0];
    const suiteScale = testsRegion ? Math.min(1, (testsRegion.r * 0.70) / Math.max(1, suiteEnclosure.r)) : 1;
    suiteCircles.forEach((packedSuite) => {
      const suite = model.suites[packedSuite.index];
      suite.x = (testsRegion?.x ?? 0) + (packedSuite.x - suiteEnclosure.x) * suiteScale;
      suite.y = (testsRegion?.y ?? 0) + (packedSuite.y - suiteEnclosure.y) * suiteScale;
      suite.r = Math.max(2.5, packedSuite.r * suiteScale);
      suite.kind = "suite";
    });

    const githubRegion = regionById.get(".github") ?? regions.find((region) => region.gates > 0);
    const qualityOutlet = {
      id: "quality-outlet",
      kind: "qualityOutlet",
      label: "quality gates",
      members: [...model.qualityGates],
      x: githubRegion ? githubRegion.x : 0,
      y: githubRegion ? githubRegion.y : -packed.r * 0.82,
      r: 13 + Math.sqrt(model.qualityGates.length) * 1.4,
      synthetic: true,
    };

    const chunkNodes = [];
    const goldenAngle = Math.PI * (3 - Math.sqrt(5));
    for (const file of model.files) {
      const chunkIndices = file.chunks ?? [];
      const count = chunkIndices.length;
      if (count === 0) continue;
      const seedAngle = ((file.seed % 3600) / 3600) * Math.PI * 2;
      const maxRadius = file.r * 0.73;
      const chunkRadius = Math.max(0.20, Math.min(0.85, file.r / (Math.sqrt(count) * 2.45 + 1)));
      for (let localIndex = 0; localIndex < count; localIndex += 1) {
        const chunkIndex = chunkIndices[localIndex];
        const chunk = model.chunks[chunkIndex];
        const radial = count === 1 ? 0 : maxRadius * Math.sqrt((localIndex + 0.45) / count);
        const angle = seedAngle + localIndex * goldenAngle;
        chunk.index = chunkIndex;
        chunk.x = file.x + radial * Math.cos(angle);
        chunk.y = file.y + radial * Math.sin(angle);
        chunk.r = chunkRadius;
        chunk.kindVisual = "chunk";
        chunkNodes.push(chunk);
      }
    }

    const routeData = buildImportRoutes(regionById);
    const testRoutes = model.testEdges.map((edge) => {
      const source = model.files[edge.source];
      const target = model.files[edge.target];
      const targetRegion = regionById.get(target.region);
      return {
        ...edge,
        kind: "test-edge",
        points: curvedRoute(source, target, edge.id, targetRegion ? [{ x: targetRegion.x, y: targetRegion.y }] : []),
      };
    });

    const externalRoutes = model.externalEdges.map((edge) => {
      const source = model.externals[edge.sourceExternal];
      const target = model.files[edge.target];
      const targetRegion = regionById.get(target.region);
      const via = targetRegion ? [pointToward(source, targetRegion, 0.52)] : [];
      return {
        ...edge,
        kind: "external-edge",
        points: curvedRoute(source, target, edge.id, via),
      };
    });

    const suiteCollectionRoutes = [];
    for (const suite of model.suites) {
      for (const fileIndex of suite.members) {
        const file = model.files[fileIndex];
        suiteCollectionRoutes.push({
          id: `collection:${fileIndex}:${suite.index}`,
          kind: "collection-edge",
          source: fileIndex,
          targetSuite: suite.index,
          inferred: true,
          points: curvedRoute(file, suite, `collection:${fileIndex}`, []),
        });
      }
    }

    const suiteOutletRoutes = model.suites.map((suite) => ({
      id: `suite-outlet:${suite.index}`,
      kind: "collection-edge",
      sourceSuite: suite.index,
      targetOutlet: qualityOutlet.id,
      inferred: true,
      points: curvedRoute(suite, qualityOutlet, `suite-outlet:${suite.index}`, []),
      demand: suite.members.length,
    }));

    const gateCollectionRoutes = model.qualityGates.map((fileIndex) => ({
      id: `gate-collection:${fileIndex}`,
      kind: "collection-edge",
      sourceOutlet: qualityOutlet.id,
      target: fileIndex,
      inferred: true,
      points: curvedRoute(qualityOutlet, model.files[fileIndex], `gate:${fileIndex}`, []),
    }));

    const bundleRoutes = {
      import: model.importRegionBundles.map((bundle) => ({
        ...bundle,
        kind: "import-bundle",
        points: regionBundlePoints(regionById.get(bundle.sourceRegion), regionById.get(bundle.targetRegion), bundle.id),
      })),
      external: model.externalRegionBundles.map((bundle) => {
        const target = regionById.get(bundle.targetRegion);
        const sourceEdges = bundle.edgeIds
          .map((edgeId) => model.externalEdges[Number(edgeId.split(":").at(-1))])
          .filter(Boolean);
        const packageIds = [...new Set(sourceEdges.map((edge) => edge.sourceExternal))];
        const source = packageIds.length > 0
          ? {
              x: d3.mean(packageIds, (index) => model.externals[index].x),
              y: d3.mean(packageIds, (index) => model.externals[index].y),
            }
          : { x: 0, y: -packed.r - 145 };
        return { ...bundle, kind: "external-bundle", packageIds, points: regionBundlePoints(source, target, bundle.id) };
      }),
      test: model.testRegionBundles.map((bundle) => ({
        ...bundle,
        kind: "test-bundle",
        points: regionBundlePoints(regionById.get(bundle.sourceRegion), regionById.get(bundle.targetRegion), bundle.id),
      })),
    };

    const externalMaxRadius = d3.max(model.externals, (external) => Math.hypot(external.x, external.y) + external.r) ?? packed.r;
    const boundsRadius = Math.max(packed.r, externalMaxRadius, Math.hypot(qualityOutlet.x, qualityOutlet.y) + qualityOutlet.r) + 40;

    return {
      packed,
      directories,
      regions,
      regionById,
      chunkNodes,
      qualityOutlet,
      primaryRoutes: routeData.primaryRoutes,
      secondaryRoutes: routeData.secondaryRoutes,
      trunks: routeData.trunks,
      junctions: routeData.junctions,
      routeByEdgeId: routeData.routeByEdgeId,
      externalRoutes,
      testRoutes,
      suiteCollectionRoutes,
      suiteOutletRoutes,
      gateCollectionRoutes,
      bundleRoutes,
      bounds: { x0: -boundsRadius, y0: -boundsRadius, x1: boundsRadius, y1: boundsRadius },
    };
  }

  function buildImportRoutes(regionById) {
    const primaryBySourceAndRegion = new Map();
    const secondaryRoutes = [];
    const routeByEdgeId = new Map();

    for (const edge of model.importEdges) {
      const source = model.files[edge.source];
      const target = model.files[edge.target];
      if (!edge.primary) {
        const route = { ...edge, kind: "import-edge", points: curvedRoute(source, target, edge.id, []) };
        secondaryRoutes.push(route);
        routeByEdgeId.set(edge.id, route);
        continue;
      }
      const key = `${edge.source}:${target.region}`;
      if (!primaryBySourceAndRegion.has(key)) primaryBySourceAndRegion.set(key, []);
      primaryBySourceAndRegion.get(key).push(edge);
    }

    const primaryRoutes = [];
    const trunks = [];
    const junctions = [];
    for (const [groupKey, edges] of primaryBySourceAndRegion) {
      const source = model.files[edges[0].source];
      if (edges.length < 4) {
        for (const edge of edges) {
          const target = model.files[edge.target];
          const route = { ...edge, kind: "import-edge", points: curvedRoute(source, target, edge.id, []) };
          primaryRoutes.push(route);
          routeByEdgeId.set(edge.id, route);
        }
        continue;
      }

      const totalDemand = d3.sum(edges, (edge) => edge.demand);
      const centroid = {
        x: d3.sum(edges, (edge) => model.files[edge.target].x * edge.demand) / Math.max(1, totalDemand),
        y: d3.sum(edges, (edge) => model.files[edge.target].y * edge.demand) / Math.max(1, totalDemand),
      };
      const targetRegion = regionById.get(model.files[edges[0].target].region);
      const anchor = targetRegion ? pointToward(source, targetRegion, source.region === targetRegion.id ? 0.32 : 0.58) : pointToward(source, centroid, 0.55);
      const offset = perpendicularOffset(source, centroid, groupKey, Math.min(36, distance(source, centroid) * 0.10));
      const junction = {
        id: `junction:${groupKey}`,
        kind: "junction",
        synthetic: true,
        source: source.index,
        targetRegion: model.files[edges[0].target].region,
        memberEdgeIds: edges.map((edge) => edge.id),
        x: anchor.x + offset.x,
        y: anchor.y + offset.y,
        r: 2.8 + Math.min(6, Math.sqrt(edges.length)),
        demand: totalDemand,
      };
      junctions.push(junction);
      trunks.push({
        id: `trunk:${groupKey}`,
        kind: "trunk",
        source: source.index,
        targetJunction: junction.id,
        memberEdgeIds: junction.memberEdgeIds,
        demand: totalDemand,
        points: curvedRoute(source, junction, `trunk:${groupKey}`, []),
      });
      for (const edge of edges) {
        const target = model.files[edge.target];
        const route = { ...edge, kind: "import-edge", junction: junction.id, points: curvedRoute(junction, target, edge.id, []) };
        primaryRoutes.push(route);
        routeByEdgeId.set(edge.id, route);
      }
    }

    return { primaryRoutes, secondaryRoutes, trunks, junctions, routeByEdgeId };
  }

  function regionBundlePoints(source, target, key) {
    if (!source || !target) return [];
    const midpoint = { x: (source.x + target.x) / 2, y: (source.y + target.y) / 2 };
    const offset = perpendicularOffset(source, target, key, Math.min(90, distance(source, target) * 0.12));
    return [source, { x: midpoint.x + offset.x, y: midpoint.y + offset.y }, target];
  }

  function curvedRoute(source, target, key, via) {
    const points = [{ x: source.x, y: source.y }];
    if (via.length > 0) {
      for (const point of via) points.push({ x: point.x, y: point.y });
    } else {
      const midpoint = { x: (source.x + target.x) / 2, y: (source.y + target.y) / 2 };
      const offset = perpendicularOffset(source, target, key, Math.min(42, distance(source, target) * 0.11));
      points.push({ x: midpoint.x + offset.x, y: midpoint.y + offset.y });
    }
    points.push({ x: target.x, y: target.y });
    return points;
  }

  function perpendicularOffset(source, target, key, magnitude) {
    const dx = target.x - source.x;
    const dy = target.y - source.y;
    const length = Math.hypot(dx, dy) || 1;
    const sign = model.stableNumber(key) % 2 === 0 ? 1 : -1;
    return { x: (-dy / length) * magnitude * sign, y: (dx / length) * magnitude * sign };
  }

  function pointToward(source, target, amount) {
    return { x: source.x + (target.x - source.x) * amount, y: source.y + (target.y - source.y) * amount };
  }

  function distance(a, b) {
    return Math.hypot(a.x - b.x, a.y - b.y);
  }

  function configureInterface() {
    const meta = data.metadata;
    document.querySelector("#repository-caption").textContent = `${meta.repositoryName} · commit ${String(meta.commit).slice(0, 12)} · D3 ${meta.d3Version}`;
    document.querySelector("#coverage-label").textContent = `${formatNumber(meta.counts.files + meta.counts.chunks + meta.counts.externalPackages)} / ${formatNumber(meta.counts.files + meta.counts.chunks + meta.counts.externalPackages)}`;

    const stats = [
      [meta.counts.files, "files"],
      [meta.counts.chunks, "chunks"],
      [meta.counts.internalImports, "imports"],
      [meta.counts.externalImports, "external"],
      [meta.counts.explicitTests, "test maps"],
      [meta.counts.externalPackages, "packages"],
    ];
    document.querySelector("#stats-grid").innerHTML = stats.map(([value, label]) => `<div class="stat-card"><strong>${formatNumber(value)}</strong><span>${escapeHtml(label)}</span></div>`).join("");

    const layerDefinitions = [
      ["regions", "Structure", "Region and repository boundaries", layout.regions.length, "neutral"],
      ["directories", "Directories", "Directory boundaries at close zoom", layout.directories.length, "neutral"],
      ["nodes", "Files", "Every file remains visible", model.files.length, "neutral"],
      ["primary", "Primary imports", "Selected provider → consumer forest", layout.primaryRoutes.length, "import"],
      ["secondary", "Secondary imports", "All preserved non-primary imports", layout.secondaryRoutes.length, "import"],
      ["external", "External imports", "External package → consumer", layout.externalRoutes.length, "import"],
      ["test", "Test edges", "Explicit subject → test mappings", layout.testRoutes.length, "test"],
      ["collections", "Test suites & gates", "Projection-only suites and gates", model.suites.length, "test"],
      ["chunks", "Symbols", "All chunks at close zoom", model.chunks.length, "neutral"],
      ["labels", "Labels", "Adaptive region and artifact names", null, "neutral"],
      ["motion", "Flow direction", "Animated projected direction", null, "neutral"],
    ];
    const layerControls = document.querySelector("#layer-controls");
    layerControls.innerHTML = layerDefinitions.map(([key, label, description, count, family]) => `
      <label class="layer-row" data-family="${family}">
        <input type="checkbox" data-layer="${key}" ${state.layers[key] ? "checked" : ""}>
        <span class="layer-switch" aria-hidden="true"></span>
        <span class="layer-label"><strong>${escapeHtml(label)}</strong><small>${escapeHtml(description)}</small></span>
        ${count == null ? "" : `<span class="layer-count">${formatNumber(count)}</span>`}
      </label>`).join("");

    layerControls.addEventListener("change", (event) => {
      const input = event.target.closest("input[data-layer]");
      if (!input) return;
      state.layers[input.dataset.layer] = input.checked;
      scheduleDraw();
      updateAnimationLoop();
    });

    document.querySelectorAll(".projection-button").forEach((button) => {
      button.addEventListener("click", () => setMode(button.dataset.mode));
    });
    document.querySelector("#detail-select").addEventListener("change", (event) => {
      state.detail = event.target.value;
      scheduleDraw();
    });
    const themeSelect = document.querySelector("#theme-select");
    if (themeSelect) {
      themeSelect.innerHTML = CartogramThemes.list()
        .map((theme) => `<option value="${escapeHtml(theme.id)}">${escapeHtml(theme.label)}</option>`)
        .join("");
      themeSelect.value = activeTheme;
      themeSelect.addEventListener("change", (event) => applyTheme(event.target.value, activeMode, true));
    }
    const modeToggle = document.querySelector("#mode-toggle");
    if (modeToggle) {
      const syncModeToggle = () => modeToggle.setAttribute("aria-pressed", String(activeMode === "light"));
      syncModeToggle();
      modeToggle.addEventListener("click", () => {
        applyTheme(activeTheme, activeMode === "dark" ? "light" : "dark", true);
        syncModeToggle();
      });
    }
    document.querySelector("#defaults-button").addEventListener("click", resetLayers);
    document.querySelector("#fit-button").addEventListener("click", () => fitAtlas(true));
    document.querySelector("#export-button").addEventListener("click", exportPng);
    document.querySelector("#help-button").addEventListener("click", () => setModal(true));
    document.querySelectorAll("[data-close-modal]").forEach((button) => button.addEventListener("click", () => setModal(false)));
    document.querySelector("#help-dialog").addEventListener("click", (event) => {
      if (event.target.id === "help-dialog") setModal(false);
    });
    document.querySelector("#legend-toggle").addEventListener("click", (event) => {
      const content = document.querySelector("#legend-content");
      const collapsed = content.classList.toggle("collapsed");
      event.currentTarget.textContent = collapsed ? "Expand" : "Collapse";
      event.currentTarget.setAttribute("aria-expanded", String(!collapsed));
    });

    searchInput.addEventListener("input", updateSearchResults);
    searchInput.addEventListener("keydown", handleSearchKeyboard);
    searchResults.addEventListener("click", (event) => {
      const button = event.target.closest("button[data-search-index]");
      if (!button) return;
      chooseSearchResult(Number(button.dataset.searchIndex));
    });

    window.addEventListener("resize", resize);
    window.addEventListener("keydown", handleKeyboard);
    document.addEventListener("visibilitychange", updateAnimationLoop);
    updateAnimationLoop();
  }

  function configureZoom() {
    const zoom = d3.zoom()
      .scaleExtent([0.18, 34])
      .filter((event) => {
        if (event.type === "wheel") return !event.ctrlKey || event.ctrlKey;
        return !event.button;
      })
      .on("start", (event) => {
        if (event.sourceEvent) state.pointerDown = { x: event.sourceEvent.clientX, y: event.sourceEvent.clientY };
      })
      .on("zoom", (event) => {
        state.transform = event.transform;
        zoomReadout.textContent = `${event.transform.k.toFixed(event.transform.k < 1 ? 2 : 1)}×`;
        hideTooltip();
        scheduleDraw();
      });

    interactionLayer.call(zoom);
    state.zoom = zoom;

    interactionLayer.on("mousemove.atlas", (event) => handlePointerMove(event));
    interactionLayer.on("mouseleave.atlas", () => {
      state.hovered = null;
      hideTooltip();
      scheduleDraw();
    });
    interactionLayer.on("click.atlas", (event) => {
      const moved = state.pointerDown && Math.hypot(event.clientX - state.pointerDown.x, event.clientY - state.pointerDown.y) > 5;
      state.pointerDown = null;
      if (moved) return;
      const target = findTarget(event);
      select(target, { focus: false });
    });
    interactionLayer.on("dblclick.atlas", (event) => {
      event.preventDefault();
      const target = findTarget(event);
      if (target) focusSelection(target, true);
    });
  }

  function resize() {
    state.width = window.innerWidth;
    state.height = window.innerHeight;
    state.dpr = Math.min(2, window.devicePixelRatio || 1);
    canvas.width = Math.round(state.width * state.dpr);
    canvas.height = Math.round(state.height * state.dpr);
    canvas.style.width = `${state.width}px`;
    canvas.style.height = `${state.height}px`;
    scheduleDraw();
  }

  function setMode(mode) {
    if (!new Set(["combined", "import", "test"]).has(mode)) return;
    state.mode = mode;
    document.querySelectorAll(".projection-button").forEach((button) => {
      const active = button.dataset.mode === mode;
      button.classList.toggle("active", active);
      button.setAttribute("aria-pressed", String(active));
    });
    scheduleDraw();
  }

  function resetLayers() {
    Object.assign(state.layers, {
      regions: true,
      directories: true,
      nodes: true,
      primary: true,
      secondary: true,
      external: true,
      test: true,
      collections: true,
      chunks: true,
      labels: true,
      motion: !window.matchMedia("(prefers-reduced-motion: reduce)").matches,
    });
    document.querySelectorAll("input[data-layer]").forEach((input) => {
      input.checked = state.layers[input.dataset.layer];
    });
    state.detail = "adaptive";
    document.querySelector("#detail-select").value = state.detail;
    scheduleDraw();
    updateAnimationLoop();
  }

  function fitAtlas(animated) {
    const left = 324;
    const right = 356;
    const top = 108;
    const bottom = 24;
    const availableWidth = Math.max(200, state.width - left - right);
    const availableHeight = Math.max(200, state.height - top - bottom);
    const boundsWidth = layout.bounds.x1 - layout.bounds.x0;
    const boundsHeight = layout.bounds.y1 - layout.bounds.y0;
    const scale = Math.min(availableWidth / boundsWidth, availableHeight / boundsHeight) * 0.96;
    const centerX = (layout.bounds.x0 + layout.bounds.x1) / 2;
    const centerY = (layout.bounds.y0 + layout.bounds.y1) / 2;
    const target = d3.zoomIdentity
      .translate(left + availableWidth / 2, top + availableHeight / 2)
      .scale(scale)
      .translate(-centerX, -centerY);
    const selection = interactionLayer;
    if (animated) selection.transition().duration(650).ease(d3.easeCubicOut).call(state.zoom.transform, target);
    else selection.call(state.zoom.transform, target);
  }

  function focusSelection(selection, animated) {
    if (!selection) return;
    const point = selectionPoint(selection);
    if (!point) return;
    const desiredScale = selection.kind === "chunk" ? 12 : selection.kind === "file" ? Math.max(4, Math.min(10, 85 / Math.max(4, model.files[selection.index].r))) : selection.kind === "region" ? Math.max(0.7, 320 / Math.max(80, point.r)) : 4;
    const viewportCenter = {
      x: (324 + Math.max(200, state.width - 324 - 356) / 2),
      y: (108 + Math.max(200, state.height - 108 - 24) / 2),
    };
    const target = d3.zoomIdentity.translate(viewportCenter.x, viewportCenter.y).scale(desiredScale).translate(-point.x, -point.y);
    if (animated) interactionLayer.transition().duration(650).ease(d3.easeCubicOut).call(state.zoom.transform, target);
    else interactionLayer.call(state.zoom.transform, target);
  }

  function scheduleDraw() {
    if (state.drawQueued) return;
    state.drawQueued = true;
    requestAnimationFrame((time) => {
      state.drawQueued = false;
      draw(time);
    });
  }

  function draw(time = performance.now()) {
    const ctx = context;
    const { width, height, dpr, transform } = state;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, width, height);
    drawScreenBackground(ctx, width, height);

    ctx.save();
    ctx.translate(transform.x, transform.y);
    ctx.scale(transform.k, transform.k);

    const detail = detailLevel();
    if (state.layers.regions) drawRegions(ctx, detail);
    if (state.layers.directories && detail.directories) drawDirectories(ctx);

    if (state.mode !== "test") drawImport(ctx, detail, time);
    if (state.mode !== "import" && state.layers.test) drawTest(ctx, detail, time);
    if (state.layers.nodes) drawFiles(ctx, detail);
    if (state.mode !== "import" && state.layers.collections) drawQualityCollectors(ctx, detail);
    if (state.layers.chunks && detail.chunks) drawChunks(ctx);

    drawSelection(ctx);
    if (state.layers.labels) drawLabels(ctx, detail);
    ctx.restore();
  }

  function drawScreenBackground(ctx, width, height) {
    const gradient = ctx.createRadialGradient(width * 0.52, height * 0.48, 20, width * 0.52, height * 0.48, Math.max(width, height) * 0.72);
    gradient.addColorStop(0, "rgba(17, 42, 55, 0.16)");
    gradient.addColorStop(1, "rgba(2, 7, 12, 0.10)");
    ctx.fillStyle = gradient;
    ctx.fillRect(0, 0, width, height);

    ctx.save();
    ctx.strokeStyle = COLORS.grid;
    ctx.lineWidth = 1;
    const spacing = 38;
    for (let x = (state.transform.x % spacing); x < width; x += spacing) {
      ctx.beginPath();
      ctx.moveTo(x, 0);
      ctx.lineTo(x, height);
      ctx.stroke();
    }
    for (let y = (state.transform.y % spacing); y < height; y += spacing) {
      ctx.beginPath();
      ctx.moveTo(0, y);
      ctx.lineTo(width, y);
      ctx.stroke();
    }
    ctx.restore();
  }

  function detailLevel() {
    const k = state.transform.k;
    if (state.detail === "full") return { overview: false, edges: true, directories: true, chunks: k > 0.85, labels: "full" };
    if (state.detail === "overview") return { overview: true, edges: false, directories: false, chunks: false, labels: "overview" };
    return {
      overview: k < 0.70,
      edges: k >= 0.54,
      directories: k >= 1.35,
      chunks: k >= 2.8,
      labels: k < 0.85 ? "overview" : k < 2.5 ? "medium" : "full",
    };
  }

  function drawRegions(ctx, detail) {
    for (const region of layout.regions) {
      if (!visibleCircle(region.x, region.y, region.r)) continue;
      ctx.beginPath();
      ctx.arc(region.x, region.y, region.r, 0, Math.PI * 2);
      ctx.fillStyle = colorWithAlpha(region.color, state.mode === "test" ? 0.075 : 0.095);
      ctx.fill();
      ctx.strokeStyle = colorWithAlpha(region.color, 0.32);
      ctx.lineWidth = screenWidth(detail.overview ? 1.4 : 1.0);
      ctx.stroke();
    }
  }

  function drawDirectories(ctx) {
    const k = state.transform.k;
    for (const directory of layout.directories) {
      if (directory.depth < 2 || directory.depth > (k > 4 ? 5 : 3)) continue;
      if (!visibleCircle(directory.x, directory.y, directory.r)) continue;
      ctx.beginPath();
      ctx.arc(directory.x, directory.y, directory.r, 0, Math.PI * 2);
      ctx.strokeStyle = `rgba(150, 191, 207, ${directory.depth === 2 ? 0.14 : 0.075})`;
      ctx.lineWidth = screenWidth(0.65);
      ctx.setLineDash([screenWidth(2.2), screenWidth(3.4)]);
      ctx.stroke();
      ctx.setLineDash([]);
    }
  }

  function drawImport(ctx, detail, time) {
    if (detail.overview) {
      if (state.layers.primary || state.layers.secondary) {
        for (const bundle of layout.bundleRoutes.import) {
          drawRoute(ctx, bundle.points, {
            color: COLORS.import,
            width: 1.1 + Math.sqrt(bundle.count) * 0.58,
            alpha: Math.min(0.52, 0.13 + Math.log1p(bundle.count) * 0.08),
            glow: true,
          });
        }
      }
      if (state.layers.external) {
        for (const bundle of layout.bundleRoutes.external) {
          drawRoute(ctx, bundle.points, { color: COLORS.external, width: 0.75 + Math.sqrt(bundle.count) * 0.28, alpha: 0.20, glow: false });
        }
        for (const external of model.externals) {
          if (!visibleCircle(external.x, external.y, external.r)) continue;
          ctx.beginPath();
          ctx.arc(external.x, external.y, Math.max(screenWidth(1.2), external.r * 0.48), 0, Math.PI * 2);
          ctx.fillStyle = colorWithAlpha(COLORS.external, external.consumers.length ? 0.58 : 0.16);
          ctx.fill();
        }
      }
      if (state.layers.motion) drawFlowParticles(ctx, time, "import-overview", layout.bundleRoutes.import, COLORS.importHot, 30);
      return;
    }

    if (state.layers.secondary && detail.edges) {
      for (const route of layout.secondaryRoutes) {
        if (!routeVisible(route.points)) continue;
        drawRoute(ctx, route.points, {
          color: COLORS.secondaryImport,
          width: 0.55,
          alpha: route.cycleCut ? 0.55 : 0.16,
          dash: [2.2, 3.2],
        });
      }
    }

    if (state.layers.primary && detail.edges) {
      for (const trunk of layout.trunks) {
        if (!routeVisible(trunk.points)) continue;
        drawRoute(ctx, trunk.points, {
          color: COLORS.import,
          width: 1.2 + Math.pow(Math.max(1, trunk.demand), 1 / 3) * 0.62,
          alpha: 0.70,
          glow: true,
        });
      }
      for (const route of layout.primaryRoutes) {
        if (!routeVisible(route.points)) continue;
        drawRoute(ctx, route.points, {
          color: demandColor(route.demand),
          width: 0.65 + Math.pow(Math.max(1, route.demand), 1 / 3) * 0.34,
          alpha: 0.68,
          glow: route.demand > 5,
        });
      }
      for (const junction of layout.junctions) {
        if (!visibleCircle(junction.x, junction.y, junction.r)) continue;
        drawDiamond(ctx, junction.x, junction.y, screenWidth(2.3), COLORS.importHot, 0.84);
      }
    }

    if (state.layers.external && detail.edges) {
      for (const route of layout.externalRoutes) {
        if (!routeVisible(route.points)) continue;
        drawRoute(ctx, route.points, { color: COLORS.external, width: 0.72 + Math.pow(route.demand, 1 / 3) * 0.22, alpha: 0.30, glow: false });
      }
      for (const external of model.externals) {
        if (!visibleCircle(external.x, external.y, external.r)) continue;
        ctx.beginPath();
        ctx.arc(external.x, external.y, external.r, 0, Math.PI * 2);
        ctx.fillStyle = colorWithAlpha(COLORS.external, external.consumers.length ? 0.72 : 0.18);
        ctx.fill();
        ctx.strokeStyle = colorWithAlpha(COLORS.external, 0.76);
        ctx.lineWidth = screenWidth(0.85);
        ctx.stroke();
      }
    }

    if (state.layers.motion) {
      const motionRoutes = layout.trunks.length > 0 ? layout.trunks : layout.primaryRoutes;
      drawFlowParticles(ctx, time, "import", motionRoutes, COLORS.importHot, 90);
    }
  }

  function drawTest(ctx, detail, time) {
    if (detail.overview) {
      for (const bundle of layout.bundleRoutes.test) {
        drawRoute(ctx, bundle.points, {
          color: COLORS.test,
          width: 1.4 + Math.sqrt(bundle.count) * 0.9,
          alpha: 0.66,
          glow: true,
        });
      }
      if (state.layers.collections && model.testFiles.length > 0) {
        const testsRegion = layout.regionById.get("tests");
        if (testsRegion) {
          drawRoute(ctx, regionBundlePoints(testsRegion, layout.qualityOutlet, "quality-overview"), {
            color: COLORS.collection,
            width: 2.2 + Math.sqrt(model.testFiles.length) * 0.10,
            alpha: 0.28,
            dash: [2, 3],
          });
        }
      }
      if (state.layers.motion) drawFlowParticles(ctx, time, "test-overview", layout.bundleRoutes.test, COLORS.testSoft, 18);
      return;
    }

    if (detail.edges) {
      for (const route of layout.testRoutes) {
        if (!routeVisible(route.points)) continue;
        drawRoute(ctx, route.points, { color: COLORS.test, width: 1.15, alpha: 0.76, glow: true });
      }
    }

    if (state.layers.motion) drawFlowParticles(ctx, time, "test", layout.testRoutes, COLORS.testSoft, 24);
  }

  function drawQualityCollectors(ctx, detail) {
    if (!detail.edges && !detail.overview) return;
    if (!detail.overview) {
      const showMemberCollections = state.detail === "full" || state.transform.k >= 1.8;
      if (showMemberCollections) {
        for (const route of layout.suiteCollectionRoutes) {
          if (!routeVisible(route.points)) continue;
          drawRoute(ctx, route.points, { color: COLORS.collection, width: 0.42, alpha: 0.10, dash: [1.2, 2.8] });
        }
      }
      for (const route of layout.suiteOutletRoutes) {
        if (!routeVisible(route.points)) continue;
        drawRoute(ctx, route.points, { color: COLORS.collection, width: 0.5 + Math.sqrt(route.demand) * 0.10, alpha: 0.12, dash: [1.4, 3.2] });
      }
      for (const route of layout.gateCollectionRoutes) {
        if (!routeVisible(route.points)) continue;
        drawRoute(ctx, route.points, { color: COLORS.collection, width: 0.55, alpha: 0.25, dash: [1.3, 2.6] });
      }
    }

    for (const suite of model.suites) {
      if (!visibleCircle(suite.x, suite.y, suite.r)) continue;
      ctx.beginPath();
      ctx.arc(suite.x, suite.y, Math.max(screenWidth(2.8), suite.r * 0.20), 0, Math.PI * 2);
      ctx.fillStyle = colorWithAlpha(COLORS.collection, suite.explicitMappings ? 0.66 : 0.24);
      ctx.fill();
      ctx.strokeStyle = colorWithAlpha(COLORS.testSoft, suite.explicitMappings ? 0.78 : 0.34);
      ctx.lineWidth = screenWidth(0.85);
      ctx.stroke();
    }

    const outlet = layout.qualityOutlet;
    if (visibleCircle(outlet.x, outlet.y, outlet.r)) {
      drawHexagon(ctx, outlet.x, outlet.y, Math.max(screenWidth(5), outlet.r * 0.30), COLORS.collection, 0.82);
      ctx.beginPath();
      ctx.arc(outlet.x, outlet.y, Math.max(screenWidth(8), outlet.r * 0.58), 0, Math.PI * 2);
      ctx.strokeStyle = colorWithAlpha(COLORS.collection, 0.24);
      ctx.lineWidth = screenWidth(0.8);
      ctx.stroke();
    }
  }

  function drawFiles(ctx, detail) {
    const testFocus = state.mode === "test";
    for (const file of model.files) {
      if (!visibleCircle(file.x, file.y, file.r)) continue;
      const roles = file.roles;
      let fill = fileColor(file);
      let alpha = 0.52;
      if (testFocus) {
        const mappedSubject = model.testsBySubject[file.index].length > 0;
        const isTest = roles.includes("test");
        const isGate = roles.includes("quality_gate");
        if (mappedSubject || isTest || isGate) alpha = 0.84;
        else if (roles.includes("production")) alpha = 0.19;
        else alpha = 0.10;
      } else if (state.mode === "import") {
        alpha = model.importActive[file.index] ? 0.72 : 0.16;
      } else {
        alpha = roles.includes("test") ? 0.42 : 0.54;
      }

      const radius = Math.max(screenWidth(0.85), file.r * 0.78);
      ctx.beginPath();
      ctx.arc(file.x, file.y, radius, 0, Math.PI * 2);
      ctx.fillStyle = colorWithAlpha(fill, alpha);
      ctx.fill();

      if (roles.includes("test") && state.mode !== "import") {
        ctx.strokeStyle = colorWithAlpha(COLORS.test, model.subjectsByTest[file.index].length ? 0.92 : 0.38);
        ctx.lineWidth = screenWidth(model.subjectsByTest[file.index].length ? 1.15 : 0.68);
        ctx.stroke();
        if (model.subjectsByTest[file.index].length === 0 && state.transform.k > 1.1) {
          ctx.setLineDash([screenWidth(1.4), screenWidth(1.9)]);
          ctx.beginPath();
          ctx.arc(file.x, file.y, radius + screenWidth(1.8), 0, Math.PI * 2);
          ctx.strokeStyle = colorWithAlpha(COLORS.test, 0.24);
          ctx.stroke();
          ctx.setLineDash([]);
        }
      }

      if (roles.includes("quality_gate") && state.mode !== "import") {
        drawHexagon(ctx, file.x, file.y, Math.max(screenWidth(2.8), radius * 0.58), COLORS.gate, 0.92);
      } else if (model.consumersByProvider[file.index].length >= 8 && state.mode !== "test") {
        ctx.beginPath();
        ctx.arc(file.x, file.y, radius + screenWidth(1.4), 0, Math.PI * 2);
        ctx.strokeStyle = colorWithAlpha(COLORS.importHot, 0.48);
        ctx.lineWidth = screenWidth(0.75);
        ctx.stroke();
      }
    }
  }

  function drawChunks(ctx) {
    for (const chunk of layout.chunkNodes) {
      if (!visibleCircle(chunk.x, chunk.y, chunk.r)) continue;
      ctx.beginPath();
      ctx.arc(chunk.x, chunk.y, Math.max(screenWidth(0.7), chunk.r), 0, Math.PI * 2);
      ctx.fillStyle = colorWithAlpha(chunkColor(chunk), 0.82);
      ctx.fill();
    }
  }

  function drawSelection(ctx) {
    const selections = [state.hovered, state.selected].filter(Boolean);
    selections.forEach((selection, index) => {
      const point = selectionPoint(selection);
      if (!point) return;
      const color = index === selections.length - 1 && state.selected === selection ? COLORS.selected : COLORS.testSoft;
      const radius = Math.max(screenWidth(5.5), (point.r || 3) + screenWidth(3.2));
      ctx.beginPath();
      ctx.arc(point.x, point.y, radius, 0, Math.PI * 2);
      ctx.strokeStyle = colorWithAlpha(color, index === selections.length - 1 ? 0.96 : 0.55);
      ctx.lineWidth = screenWidth(index === selections.length - 1 ? 1.6 : 0.9);
      ctx.stroke();
    });
  }

  function drawLabels(ctx, detail) {
    ctx.textBaseline = "middle";
    const regionFont = Math.max(screenWidth(10.5), 10.5 / Math.sqrt(Math.max(0.4, state.transform.k)));
    ctx.font = `700 ${regionFont}px Inter, system-ui, sans-serif`;
    ctx.textAlign = "center";
    for (const region of layout.regions) {
      if (!visibleCircle(region.x, region.y, region.r)) continue;
      drawTextHalo(ctx, region.name === "(root)" ? "repository root" : region.name, region.x, region.y - region.r + screenWidth(18), COLORS.text, colorWithAlpha(COLORS.background, 0.90));
      ctx.font = `500 ${Math.max(screenWidth(7.5), 7.5 / Math.sqrt(Math.max(0.4, state.transform.k)))}px Inter, system-ui, sans-serif`;
      drawTextHalo(ctx, `${formatNumber(region.files.length)} files`, region.x, region.y - region.r + screenWidth(31), COLORS.muted, colorWithAlpha(COLORS.background, 0.82));
      ctx.font = `700 ${regionFont}px Inter, system-ui, sans-serif`;
    }

    if (detail.labels !== "overview") {
      const fileCandidates = model.files
        .filter((file) => visibleCircle(file.x, file.y, file.r))
        .filter((file) => file.metrics.supportScore > (detail.labels === "full" ? 3.0 : 5.2) || file.roles.includes("quality_gate"))
        .sort((a, b) => b.metrics.supportScore - a.metrics.supportScore)
        .slice(0, detail.labels === "full" ? 110 : 42);
      ctx.textAlign = "left";
      ctx.font = `${screenWidth(detail.labels === "full" ? 8.5 : 9.2)}px Inter, system-ui, sans-serif`;
      for (const file of fileCandidates) {
        drawTextHalo(ctx, file.name, file.x + file.r + screenWidth(2.5), file.y, file.roles.includes("test") ? COLORS.testSoft : COLORS.text, colorWithAlpha(COLORS.background, 0.86));
      }

      if (state.mode !== "import" && state.layers.collections && state.transform.k > 1.05) {
        ctx.textAlign = "center";
        ctx.font = `600 ${screenWidth(7.7)}px Inter, system-ui, sans-serif`;
        for (const suite of model.suites.slice(0, state.transform.k > 2.2 ? 70 : 24)) {
          if (!visibleCircle(suite.x, suite.y, suite.r)) continue;
          drawTextHalo(ctx, suite.label, suite.x, suite.y - Math.max(screenWidth(5), suite.r * 0.3), COLORS.testSoft, colorWithAlpha(COLORS.background, 0.88));
        }
      }

      if (state.layers.external && state.mode !== "test" && state.transform.k > 0.78) {
        ctx.textAlign = "center";
        ctx.font = `600 ${screenWidth(8.2)}px Inter, system-ui, sans-serif`;
        for (const external of model.externals.filter((item) => item.rank < (state.transform.k > 2 ? 80 : 24))) {
          if (!visibleCircle(external.x, external.y, external.r)) continue;
          drawTextHalo(ctx, external.name, external.x, external.y - external.r - screenWidth(4), COLORS.external, colorWithAlpha(COLORS.background, 0.9));
        }
      }
    }

    for (const selection of [state.hovered, state.selected]) {
      if (!selection) continue;
      const point = selectionPoint(selection);
      const label = selectionLabel(selection);
      if (!point || !label) continue;
      ctx.textAlign = "left";
      ctx.font = `700 ${screenWidth(10.2)}px Inter, system-ui, sans-serif`;
      drawTextHalo(ctx, label, point.x + (point.r || 3) + screenWidth(6), point.y - screenWidth(5), COLORS.selected, colorWithAlpha(COLORS.background, 0.94));
    }
  }

  function drawRoute(ctx, points, options) {
    if (!points || points.length < 2) return;
    const width = screenWidth(options.width ?? 1);
    const line = d3.line().x((point) => point.x).y((point) => point.y).curve(d3.curveBundle.beta(0.82)).context(ctx);
    ctx.save();
    ctx.lineCap = "round";
    ctx.lineJoin = "round";
    if (options.dash) ctx.setLineDash(options.dash.map(screenWidth));
    if (options.glow) {
      ctx.beginPath();
      line(points);
      ctx.strokeStyle = colorWithAlpha(options.color, (options.alpha ?? 1) * 0.12);
      ctx.lineWidth = width * 4.2;
      ctx.stroke();
    }
    ctx.beginPath();
    line(points);
    ctx.strokeStyle = colorWithAlpha(options.color, options.alpha ?? 1);
    ctx.lineWidth = width;
    ctx.stroke();
    ctx.restore();
  }

  function drawFlowParticles(ctx, time, family, routes, color, maxParticles) {
    if (!routes.length || document.hidden) return;
    const ranked = routes.slice().sort((a, b) => (b.demand ?? b.count ?? 1) - (a.demand ?? a.count ?? 1)).slice(0, maxParticles);
    const period = family.includes("test") ? 4400 : 3000;
    ranked.forEach((route, index) => {
      if (!route.points || !routeVisible(route.points)) return;
      const phase = ((time / period) + ((model.stableNumber(route.id ?? index) % 1000) / 1000)) % 1;
      const point = pointOnPolyline(route.points, phase);
      ctx.beginPath();
      ctx.arc(point.x, point.y, screenWidth(family.includes("test") ? 1.45 : 1.25), 0, Math.PI * 2);
      ctx.fillStyle = colorWithAlpha(color, 0.76);
      ctx.fill();
    });
  }

  function pointOnPolyline(points, t) {
    const lengths = [];
    let total = 0;
    for (let index = 1; index < points.length; index += 1) {
      const length = distance(points[index - 1], points[index]);
      lengths.push(length);
      total += length;
    }
    let target = total * t;
    for (let index = 0; index < lengths.length; index += 1) {
      if (target <= lengths[index] || index === lengths.length - 1) {
        const local = lengths[index] === 0 ? 0 : target / lengths[index];
        return {
          x: points[index].x + (points[index + 1].x - points[index].x) * local,
          y: points[index].y + (points[index + 1].y - points[index].y) * local,
        };
      }
      target -= lengths[index];
    }
    return points.at(-1);
  }

  function drawTextHalo(ctx, text, x, y, fill, halo) {
    ctx.lineJoin = "round";
    ctx.lineWidth = screenWidth(3.6);
    ctx.strokeStyle = halo;
    ctx.strokeText(text, x, y);
    ctx.fillStyle = fill;
    ctx.fillText(text, x, y);
  }

  function drawDiamond(ctx, x, y, radius, color, alpha) {
    ctx.save();
    ctx.translate(x, y);
    ctx.rotate(Math.PI / 4);
    ctx.fillStyle = colorWithAlpha(color, alpha);
    ctx.fillRect(-radius, -radius, radius * 2, radius * 2);
    ctx.restore();
  }

  function drawHexagon(ctx, x, y, radius, color, alpha) {
    ctx.beginPath();
    for (let index = 0; index < 6; index += 1) {
      const angle = -Math.PI / 2 + index * Math.PI / 3;
      const px = x + radius * Math.cos(angle);
      const py = y + radius * Math.sin(angle);
      if (index === 0) ctx.moveTo(px, py);
      else ctx.lineTo(px, py);
    }
    ctx.closePath();
    ctx.fillStyle = colorWithAlpha(color, alpha);
    ctx.fill();
  }

  function fileColor(file) {
    if (file.roles.includes("quality_gate")) return COLORS.gate;
    if (file.roles.includes("test")) return COLORS.test;
    if (file.roles.includes("production")) return COLORS.production;
    if (file.roles.includes("documentation")) return COLORS.documentation;
    if (file.roles.includes("configuration")) return COLORS.config;
    if (file.roles.includes("asset")) return COLORS.asset;
    return COLORS.unknown;
  }

  function chunkColor(chunk) {
    const kind = String(chunk.kind).toLowerCase();
    if (kind.includes("class")) return "#ffca7a";
    if (kind.includes("function") || kind.includes("method")) return COLORS.chunk;
    if (kind.includes("file")) return "#93a9b6";
    return "#9f8dcb";
  }

  function demandColor(demand) {
    const t = Math.min(1, Math.log1p(demand) / Math.log1p(60));
    return d3.interpolateRgb(COLORS.import, COLORS.importHot)(t);
  }

  function screenWidth(pixels) {
    return pixels / Math.max(0.0001, state.transform.k);
  }

  function visibleCircle(x, y, radius) {
    const pad = Math.max(radius, 8 / state.transform.k);
    const sx = x * state.transform.k + state.transform.x;
    const sy = y * state.transform.k + state.transform.y;
    const sr = pad * state.transform.k;
    return sx + sr >= 0 && sx - sr <= state.width && sy + sr >= 0 && sy - sr <= state.height;
  }

  function routeVisible(points) {
    if (!points || points.length === 0) return false;
    const x0 = d3.min(points, (point) => point.x);
    const x1 = d3.max(points, (point) => point.x);
    const y0 = d3.min(points, (point) => point.y);
    const y1 = d3.max(points, (point) => point.y);
    const sx0 = x0 * state.transform.k + state.transform.x;
    const sx1 = x1 * state.transform.k + state.transform.x;
    const sy0 = y0 * state.transform.k + state.transform.y;
    const sy1 = y1 * state.transform.k + state.transform.y;
    return sx1 >= -20 && sx0 <= state.width + 20 && sy1 >= -20 && sy0 <= state.height + 20;
  }

  function handlePointerMove(event) {
    const target = findTarget(event, true);
    if (!sameSelection(state.hovered, target)) {
      state.hovered = target;
      scheduleDraw();
    }
    if (target) showTooltip(event, target);
    else hideTooltip();
  }

  function findTarget(event, hover = false) {
    const [worldX, worldY] = state.transform.invert(d3.pointer(event, interactionLayer.node()));
    const zoom = state.transform.k;
    const maxDistance = (hover ? 10 : 14) / zoom;

    if (state.layers.chunks && detailLevel().chunks) {
      const chunk = chunkQuadtree.find(worldX, worldY, maxDistance);
      if (chunk && Math.hypot(chunk.x - worldX, chunk.y - worldY) <= Math.max(maxDistance, chunk.r * 2)) {
        return { kind: "chunk", index: chunk.index };
      }
    }

    if (state.mode !== "import" && state.layers.collections) {
      const suite = suiteQuadtree.find(worldX, worldY, maxDistance * 1.2);
      if (suite && Math.hypot(suite.x - worldX, suite.y - worldY) <= Math.max(maxDistance, suite.r)) {
        return { kind: "suite", index: suite.index };
      }
      if (Math.hypot(layout.qualityOutlet.x - worldX, layout.qualityOutlet.y - worldY) <= Math.max(maxDistance, layout.qualityOutlet.r)) {
        return { kind: "qualityOutlet", id: layout.qualityOutlet.id };
      }
    }

    if (state.mode !== "test" && state.layers.external) {
      const external = externalQuadtree.find(worldX, worldY, maxDistance * 1.2);
      if (external && Math.hypot(external.x - worldX, external.y - worldY) <= Math.max(maxDistance, external.r)) {
        return { kind: "external", index: external.index };
      }
    }

    const file = fileQuadtree.find(worldX, worldY, maxDistance);
    if (file && Math.hypot(file.x - worldX, file.y - worldY) <= Math.max(maxDistance, file.r)) {
      return { kind: "file", index: file.index };
    }

    if (state.mode !== "test" && state.layers.primary) {
      const junction = junctionQuadtree.find(worldX, worldY, maxDistance * 1.2);
      if (junction && Math.hypot(junction.x - worldX, junction.y - worldY) <= Math.max(maxDistance, junction.r)) {
        return { kind: "junction", id: junction.id };
      }
    }

    const edge = findEdge(worldX, worldY, maxDistance);
    if (edge) return edge;

    const containingRegions = layout.regions.filter((region) => Math.hypot(region.x - worldX, region.y - worldY) <= region.r);
    if (containingRegions.length > 0) {
      containingRegions.sort((a, b) => a.r - b.r);
      return { kind: "region", id: containingRegions[0].id };
    }
    return null;
  }

  function findEdge(x, y, threshold) {
    if (detailLevel().overview) {
      const candidates = [];
      if (state.mode !== "test") candidates.push(...layout.bundleRoutes.import.map((edge) => ({ kind: "bundle", family: "import", edge })));
      if (state.mode !== "import" && state.layers.test) candidates.push(...layout.bundleRoutes.test.map((edge) => ({ kind: "bundle", family: "test", edge })));
      let best = null;
      let bestDistance = threshold;
      for (const candidate of candidates) {
        const distanceValue = distanceToPolyline({ x, y }, candidate.edge.points);
        if (distanceValue < bestDistance) {
          bestDistance = distanceValue;
          best = { kind: "bundle", family: candidate.family, id: candidate.edge.id };
        }
      }
      return best;
    }

    const candidates = [];
    if (state.mode !== "test") {
      if (state.layers.primary) candidates.push(...layout.primaryRoutes, ...layout.trunks);
      if (state.layers.secondary) candidates.push(...layout.secondaryRoutes);
      if (state.layers.external) candidates.push(...layout.externalRoutes);
    }
    if (state.mode !== "import" && state.layers.test) candidates.push(...layout.testRoutes);
    let best = null;
    let bestDistance = threshold;
    for (const route of candidates) {
      if (!routeVisible(route.points)) continue;
      const distanceValue = distanceToPolyline({ x, y }, route.points);
      if (distanceValue < bestDistance) {
        bestDistance = distanceValue;
        if (route.kind === "trunk") best = { kind: "trunk", id: route.id };
        else if (route.kind === "external-edge") best = { kind: "externalEdge", id: route.id };
        else if (route.kind === "test-edge") best = { kind: "testEdge", id: route.id };
        else best = { kind: "importEdge", id: route.id };
      }
    }
    return best;
  }

  function distanceToPolyline(point, points) {
    let minimum = Infinity;
    for (let index = 1; index < points.length; index += 1) {
      minimum = Math.min(minimum, distanceToSegment(point, points[index - 1], points[index]));
    }
    return minimum;
  }

  function distanceToSegment(point, start, end) {
    const dx = end.x - start.x;
    const dy = end.y - start.y;
    const lengthSquared = dx * dx + dy * dy;
    if (lengthSquared === 0) return Math.hypot(point.x - start.x, point.y - start.y);
    const t = Math.max(0, Math.min(1, ((point.x - start.x) * dx + (point.y - start.y) * dy) / lengthSquared));
    const x = start.x + t * dx;
    const y = start.y + t * dy;
    return Math.hypot(point.x - x, point.y - y);
  }

  function select(selection, options = {}) {
    state.selected = selection;
    renderDetails(selection);
    scheduleDraw();
    if (selection && options.focus) focusSelection(selection, true);
  }

  function showTooltip(event, selection) {
    const label = selectionLabel(selection);
    const subtitle = selectionSubtitle(selection);
    tooltip.innerHTML = `<strong>${escapeHtml(label)}</strong><span>${escapeHtml(subtitle)}</span>`;
    tooltip.hidden = false;
    const x = Math.min(state.width - 330, event.clientX + 14);
    const y = Math.min(state.height - 90, event.clientY + 14);
    tooltip.style.transform = `translate(${Math.max(8, x)}px, ${Math.max(8, y)}px)`;
  }

  function hideTooltip() {
    tooltip.hidden = true;
  }

  function renderDetails(selection) {
    if (!selection) {
      detailsPanel.innerHTML = `<div class="details-empty"><div class="empty-glyph" aria-hidden="true"></div><h2>Select any structure</h2><p>Click a file, chunk, package, region, test suite, or edge to inspect the canonical software fact and its projection.</p></div>`;
      return;
    }
    if (selection.kind === "file") renderFileDetails(model.files[selection.index]);
    else if (selection.kind === "chunk") renderChunkDetails(model.chunks[selection.index]);
    else if (selection.kind === "external") renderExternalDetails(model.externals[selection.index]);
    else if (selection.kind === "region") renderRegionDetails(layout.regionById.get(selection.id));
    else if (selection.kind === "suite") renderSuiteDetails(model.suites[selection.index]);
    else if (selection.kind === "qualityOutlet") renderQualityOutletDetails();
    else if (selection.kind === "junction") renderJunctionDetails(layout.junctions.find((item) => item.id === selection.id));
    else if (selection.kind === "importEdge") renderImportEdgeDetails(findImportRoute(selection.id));
    else if (selection.kind === "externalEdge") renderExternalEdgeDetails(layout.externalRoutes.find((route) => route.id === selection.id));
    else if (selection.kind === "testEdge") renderTestEdgeDetails(layout.testRoutes.find((route) => route.id === selection.id));
    else if (selection.kind === "trunk") renderTrunkDetails(layout.trunks.find((trunk) => trunk.id === selection.id));
    else if (selection.kind === "bundle") renderBundleDetails(selection);
  }

  function renderFileDetails(file) {
    const concepts = file.concepts.slice(0, 24).map((index) => model.concepts[index]?.label).filter(Boolean);
    const providerButtons = model.providersByConsumer[file.index].slice(0, 12).map((index) => relationButton(index, "←", "imports"));
    const dependentButtons = model.consumersByProvider[file.index].slice(0, 12).map((index) => relationButton(index, "→", "supports"));
    const testButtons = model.testsBySubject[file.index].slice(0, 12).map((index) => relationButton(index, "→", "tested by"));
    const subjectButtons = model.subjectsByTest[file.index].slice(0, 12).map((index) => relationButton(index, "→", "tests"));
    const externalNames = model.externalByConsumer[file.index].map((index) => model.externals[index].name);
    const primaryProvider = file.primaryProvider >= 0 ? model.files[file.primaryProvider] : null;
    const projectionNotes = [];
    if (primaryProvider) projectionNotes.push(`The imports view reverses the canonical import and draws ${primaryProvider.path} → ${file.path} as this file’s primary import.`);
    else if (model.importActive[file.index]) projectionNotes.push("This file is an import-graph root or receives only external imports; no primary internal provider was selected.");
    if (file.roles.includes("test")) {
      projectionNotes.push(model.subjectsByTest[file.index].length ? `The tests view reverses ${model.subjectsByTest[file.index].length} explicit test relation(s), so tested subjects point toward this test file.` : "This test artifact is shown as unmapped because the inventory provides no explicit cbm:tests target for it.");
    } else {
      projectionNotes.push(model.testsBySubject[file.index].length ? `${model.testsBySubject[file.index].length} explicit test mapping(s) drain from this artifact to test nodes.` : "No explicit test mapping in the inventory drains from this artifact; this is not a claim of zero real-world coverage.");
    }

    detailsPanel.innerHTML = `
      ${detailsHeader(file.roles.includes("test") ? "Test file" : "Software file", file.name, file.path, [...file.roles, file.type, file.region])}
      <section class="details-section"><h3>Measurements</h3><div class="metric-grid">
        ${metric(formatBytes(file.size), "size")}${metric(file.metrics.chunks, "chunks")}${metric(file.metrics.supportScore.toFixed(2), "support")}
        ${metric(file.metrics.providers, "providers")}${metric(file.metrics.dependents, "dependents")}${metric(file.metrics.transitiveDependents, "transitive")}
        ${metric(file.metrics.externalProviders, "external")}${metric(file.metrics.tests, "tests")}${metric(file.metrics.testedBy, "tested by")}
      </div></section>
      <section class="details-section"><h3>Canonical relations</h3><div class="relation-list">
        ${[...providerButtons, ...dependentButtons, ...testButtons, ...subjectButtons].join("") || `<div class="projection-note">No internal import or explicit test relation is attached to this file.</div>`}
      </div>${externalNames.length ? `<div class="badge-row">${externalNames.map((name) => `<span class="badge import">${escapeHtml(name)}</span>`).join("")}</div>` : ""}</section>
      <section class="details-section"><h3>Projection semantics</h3>${projectionNotes.map((note, index) => `<div class="projection-note ${index === 0 ? "import" : ""}">${escapeHtml(note)}</div>`).join("<div style='height:7px'></div>")}</section>
      ${concepts.length ? `<section class="details-section"><h3>Semantic concepts</h3><div class="concept-list">${concepts.map((concept) => `<span class="concept-chip">${escapeHtml(concept)}</span>`).join("")}</div></section>` : ""}
      <div class="details-actions"><button class="action-button" data-detail-action="focus">Focus</button><button class="action-button" data-detail-action="clear">Clear</button></div>`;
    bindDetailActions();
  }

  function renderChunkDetails(chunk) {
    const file = model.files[chunk.file];
    const concepts = chunk.concepts.slice(0, 24).map((index) => model.concepts[index]?.label).filter(Boolean);
    detailsPanel.innerHTML = `
      ${detailsHeader("Symbol / chunk", chunk.symbol, `${file.path}:L${chunk.begin}-L${chunk.end}`, [chunk.kind, file.region])}
      <section class="details-section"><h3>Source mapping</h3><div class="metric-grid">${metric(chunk.begin, "begin line")}${metric(chunk.end, "end line")}${metric(chunk.end - chunk.begin + 1, "lines")}</div></section>
      ${chunk.signature ? `<section class="details-section"><h3>Signature</h3><div class="details-path">${escapeHtml(String(chunk.signature))}</div></section>` : ""}
      <section class="details-section"><h3>Containing artifact</h3><div class="relation-list">${relationButton(file.index, "↑", "contained in")}</div></section>
      <section class="details-section"><h3>Projection semantics</h3><div class="projection-note">This chunk lives inside the file. It appears only at close zoom and is not itself an import or test edge.</div></section>
      ${concepts.length ? `<section class="details-section"><h3>Concepts</h3><div class="concept-list">${concepts.map((concept) => `<span class="concept-chip">${escapeHtml(concept)}</span>`).join("")}</div></section>` : ""}
      <div class="details-actions"><button class="action-button" data-detail-action="focus">Focus</button><button class="action-button" data-detail-action="clear">Clear</button></div>`;
    bindDetailActions();
  }

  function renderExternalDetails(external) {
    const consumers = external.consumers.slice(0, 24).map((index) => relationButton(index, "→", "supplies"));
    detailsPanel.innerHTML = `
      ${detailsHeader("External package", external.name, external.id, [external.version ? `v${external.version}` : "package", "external"])}
      <section class="details-section"><h3>Boundary supply</h3><div class="metric-grid">${metric(external.consumers.length, "consumers")}${metric(external.version ?? "—", "version")}${metric(external.rank + 1, "supply rank")}</div></section>
      <section class="details-section"><h3>Consumers</h3><div class="relation-list">${consumers.join("") || `<div class="projection-note">No file imports this package in the normalized inventory.</div>`}</div></section>
      <section class="details-section"><h3>Projection semantics</h3><div class="projection-note import">Canonical imports point from software files to this package. The imports view reverses them so package capability points into the repository and toward its consumers.</div></section>
      <div class="details-actions"><button class="action-button" data-detail-action="focus">Focus</button><button class="action-button" data-detail-action="clear">Clear</button></div>`;
    bindDetailActions();
  }

  function renderRegionDetails(region) {
    if (!region) return;
    const importEdges = model.importEdges.filter((edge) => model.files[edge.source].region === region.id || model.files[edge.target].region === region.id).length;
    const explicitTests = model.testEdges.filter((edge) => model.files[edge.source].region === region.id || model.files[edge.target].region === region.id).length;
    const topFiles = region.files.slice().sort((a, b) => model.files[b].metrics.supportScore - model.files[a].metrics.supportScore).slice(0, 18);
    detailsPanel.innerHTML = `
      ${detailsHeader("Region / subsystem aggregate", region.name === "(root)" ? "repository root" : region.name, region.path, ["aggregate", `${region.files.length} members`])}
      <section class="details-section"><h3>Membership</h3><div class="metric-grid">${metric(region.files.length, "files")}${metric(region.production, "production")}${metric(region.tests, "tests")}${metric(region.gates, "gates")}${metric(importEdges, "imports")}${metric(explicitTests, "tests")}</div></section>
      <section class="details-section"><h3>High-support files</h3><div class="relation-list">${topFiles.map((index) => relationButton(index, "•", "member")).join("")}</div></section>
      <section class="details-section"><h3>Projection semantics</h3><div class="projection-note">This region is an explicit path-based aggregate. Its member list is complete; the circular boundary is layout geometry rather than a software artifact.</div></section>
      <div class="details-actions"><button class="action-button" data-detail-action="focus">Focus region</button><button class="action-button" data-detail-action="clear">Clear</button></div>`;
    bindDetailActions();
  }

  function renderSuiteDetails(suite) {
    const members = suite.members.slice(0, 30).map((index) => relationButton(index, "•", model.subjectsByTest[index].length ? "mapped test" : "unmapped test"));
    detailsPanel.innerHTML = `
      ${detailsHeader("Test-suite aggregate", suite.label, suite.key, ["aggregate", "projection-only", `${suite.members.length} members`])}
      <section class="details-section"><h3>Quality collection</h3><div class="metric-grid">${metric(suite.members.length, "test files")}${metric(suite.explicitMappings, "test maps")}${metric(suite.members.length - suite.explicitMappings, "unmapped")}</div></section>
      <section class="details-section"><h3>Member tests</h3><div class="relation-list">${members.join("")}</div></section>
      <section class="details-section"><h3>Projection semantics</h3><div class="projection-note">This suite is a path-derived collection node. Dotted links are intentionally marked projection-only and do not assert a source-code call, import, or test relation.</div></section>
      <div class="details-actions"><button class="action-button" data-detail-action="focus">Focus suite</button><button class="action-button" data-detail-action="clear">Clear</button></div>`;
    bindDetailActions();
  }

  function renderQualityOutletDetails() {
    detailsPanel.innerHTML = `
      ${detailsHeader("Quality-gate aggregate", "quality gates", ".github/workflows and CI-classified files", ["aggregate", "synthetic", `${model.qualityGates.length} gates`])}
      <section class="details-section"><h3>Gate membership</h3><div class="relation-list">${model.qualityGates.slice(0, 30).map((index) => relationButton(index, "→", "gate member")).join("")}</div></section>
      <section class="details-section"><h3>Projection semantics</h3><div class="projection-note">This outlet aligns test-suite collectors with actual CI-classified files. It is a synthetic aggregate and is never counted as a repository artifact.</div></section>
      <div class="details-actions"><button class="action-button" data-detail-action="focus">Focus</button><button class="action-button" data-detail-action="clear">Clear</button></div>`;
    bindDetailActions();
  }

  function renderJunctionDetails(junction) {
    if (!junction) return;
    detailsPanel.innerHTML = `
      ${detailsHeader("Synthetic routing junction", "fan-out junction", junction.id, ["synthetic", `${junction.memberEdgeIds.length} branches`, `demand ${junction.demand.toFixed(0)}`])}
      <section class="details-section"><h3>Routing membership</h3><div class="metric-grid">${metric(junction.memberEdgeIds.length, "relations")}${metric(junction.demand.toFixed(0), "demand")}${metric(junction.targetRegion, "target region")}</div></section>
      <section class="details-section"><h3>Projection semantics</h3><div class="projection-note import">This point reduces visual fan-out. It maps to no software artifact and changes neither canonical relation count nor direction.</div></section>
      <div class="details-actions"><button class="action-button" data-detail-action="focus">Focus</button><button class="action-button" data-detail-action="clear">Clear</button></div>`;
    bindDetailActions();
  }

  function renderImportEdgeDetails(edge) {
    if (!edge) return;
    const provider = model.files[edge.source];
    const consumer = model.files[edge.target];
    detailsPanel.innerHTML = `
      ${detailsHeader(edge.primary ? "Primary import" : "Secondary import", `${provider.name} → ${consumer.name}`, edge.id, [edge.primary ? "primary" : "secondary", `demand ${edge.demand.toFixed(0)}`, edge.cycleCut ? "cycle cut" : ""])}
      <section class="details-section"><h3>Canonical software fact</h3><div class="relation-list">${relationButton(consumer.index, "→", "consumer")}${relationButton(provider.index, "→", "provider")}</div><div class="projection-note import" style="margin-top:8px">Canonical: ${escapeHtml(consumer.path)} imports ${escapeHtml(provider.path)}.<br>Projected: ${escapeHtml(provider.path)} → ${escapeHtml(consumer.path)}.<br>Direction transform: reverse.</div></section>
      <section class="details-section"><h3>Projection metrics</h3><div class="metric-grid">${metric(edge.demand.toFixed(0), "demand")}${metric(edge.primary ? "yes" : "no", "primary")}${metric(edge.cycleCut ? "yes" : "no", "cycle cut")}</div></section>
      <div class="details-actions"><button class="action-button" data-detail-action="focus">Focus target</button><button class="action-button" data-detail-action="clear">Clear</button></div>`;
    bindDetailActions();
  }

  function renderExternalEdgeDetails(edge) {
    if (!edge) return;
    const provider = model.externals[edge.sourceExternal];
    const consumer = model.files[edge.target];
    detailsPanel.innerHTML = `
      ${detailsHeader("External import edge", `${provider.name} → ${consumer.name}`, edge.id, ["external", "reverse direction"])}
      <section class="details-section"><h3>Canonical and projected direction</h3><div class="projection-note import">Canonical: ${escapeHtml(consumer.path)} imports external package ${escapeHtml(provider.name)}.<br>Projected: ${escapeHtml(provider.name)} → ${escapeHtml(consumer.path)}.</div></section>
      <section class="details-section"><h3>Endpoints</h3><div class="relation-list">${externalButton(provider.index, "source package")}${relationButton(consumer.index, "→", "consumer")}</div></section>`;
    bindDetailActions();
  }

  function renderTestEdgeDetails(edge) {
    if (!edge) return;
    const subject = model.files[edge.source];
    const test = model.files[edge.target];
    detailsPanel.innerHTML = `
      ${detailsHeader("Explicit test edge", `${subject.name} → ${test.name}`, edge.id, ["explicit cbm:tests", "reverse direction"])}
      <section class="details-section"><h3>Canonical software fact</h3><div class="projection-note">Canonical: ${escapeHtml(test.path)} tests ${escapeHtml(subject.path)}.<br>Projected: ${escapeHtml(subject.path)} → ${escapeHtml(test.path)}.<br>Direction transform: reverse.</div></section>
      <section class="details-section"><h3>Endpoints</h3><div class="relation-list">${relationButton(subject.index, "→", "tested subject")}${relationButton(test.index, "→", "test node")}</div></section>`;
    bindDetailActions();
  }

  function renderTrunkDetails(trunk) {
    if (!trunk) return;
    const source = model.files[trunk.source];
    detailsPanel.innerHTML = `
      ${detailsHeader("Aggregated import trunk", source.name, trunk.id, ["synthetic routing", `${trunk.memberEdgeIds.length} relations`, `demand ${trunk.demand.toFixed(0)}`])}
      <section class="details-section"><h3>Projection semantics</h3><div class="projection-note import">This shared segment bundles primary import branches leaving the same provider toward one region. Every canonical import remains individually recoverable at the branch.</div></section>
      <section class="details-section"><h3>Source artifact</h3><div class="relation-list">${relationButton(source.index, "→", "provider")}</div></section>`;
    bindDetailActions();
  }

  function renderBundleDetails(selection) {
    const collection = selection.family === "import" ? layout.bundleRoutes.import : layout.bundleRoutes.test;
    const bundle = collection.find((item) => item.id === selection.id);
    if (!bundle) return;
    detailsPanel.innerHTML = `
      ${detailsHeader(`${selection.family} region bundle`, `${bundle.sourceRegion} → ${bundle.targetRegion}`, bundle.id, ["overview aggregate", `${bundle.count} relations`])}
      <section class="details-section"><h3>Aggregate metrics</h3><div class="metric-grid">${metric(bundle.count, "relations")}${metric(bundle.demand.toFixed(0), "demand")}${metric("0", "lost facts")}</div></section>
      <section class="details-section"><h3>Projection semantics</h3><div class="projection-note">This low-zoom bundle is a level-of-detail representation. Zooming in restores each mapped relation; no relation is removed from the model.</div></section>`;
  }

  function bindDetailActions() {
    detailsPanel.querySelectorAll("button[data-file-index]").forEach((button) => {
      button.addEventListener("click", () => select({ kind: "file", index: Number(button.dataset.fileIndex) }, { focus: true }));
    });
    detailsPanel.querySelectorAll("button[data-external-index]").forEach((button) => {
      button.addEventListener("click", () => select({ kind: "external", index: Number(button.dataset.externalIndex) }, { focus: true }));
    });
    detailsPanel.querySelectorAll("button[data-detail-action]").forEach((button) => {
      button.addEventListener("click", () => {
        if (button.dataset.detailAction === "clear") select(null);
        else focusSelection(state.selected, true);
      });
    });
  }

  function detailsHeader(kicker, title, pathValue, badges) {
    return `<header class="details-header"><div class="details-kicker"><span>${escapeHtml(kicker)}</span><span>auditable</span></div><h2>${escapeHtml(title)}</h2><div class="details-path">${escapeHtml(pathValue || "")}</div><div class="badge-row">${badges.filter(Boolean).map((badge) => `<span class="badge ${String(badge).includes("primary") ? "import" : String(badge).includes("test") ? "test" : ""}">${escapeHtml(String(badge))}</span>`).join("")}</div></header>`;
  }

  function metric(value, label) {
    return `<div class="metric"><strong>${escapeHtml(String(value))}</strong><span>${escapeHtml(label)}</span></div>`;
  }

  function relationButton(fileIndex, arrow, kind) {
    const file = model.files[fileIndex];
    return `<button class="relation-button" data-file-index="${fileIndex}"><span class="relation-arrow">${escapeHtml(arrow)}</span><span class="relation-label">${escapeHtml(file.path)}</span><span class="relation-kind">${escapeHtml(kind)}</span></button>`;
  }

  function externalButton(index, kind) {
    const external = model.externals[index];
    return `<button class="relation-button" data-external-index="${index}"><span class="relation-arrow">◉</span><span class="relation-label">${escapeHtml(external.name)}</span><span class="relation-kind">${escapeHtml(kind)}</span></button>`;
  }

  function findImportRoute(id) {
    return layout.routeByEdgeId.get(id) ?? layout.secondaryRoutes.find((route) => route.id === id);
  }

  function selectionPoint(selection) {
    if (!selection) return null;
    if (selection.kind === "file") return model.files[selection.index];
    if (selection.kind === "chunk") return model.chunks[selection.index];
    if (selection.kind === "external") return model.externals[selection.index];
    if (selection.kind === "region") return layout.regionById.get(selection.id);
    if (selection.kind === "suite") return model.suites[selection.index];
    if (selection.kind === "qualityOutlet") return layout.qualityOutlet;
    if (selection.kind === "junction") return layout.junctions.find((item) => item.id === selection.id);
    if (selection.kind === "importEdge") {
      const edge = findImportRoute(selection.id);
      return edge ? pointOnPolyline(edge.points, 0.5) : null;
    }
    if (selection.kind === "externalEdge") {
      const edge = layout.externalRoutes.find((item) => item.id === selection.id);
      return edge ? pointOnPolyline(edge.points, 0.5) : null;
    }
    if (selection.kind === "testEdge") {
      const edge = layout.testRoutes.find((item) => item.id === selection.id);
      return edge ? pointOnPolyline(edge.points, 0.5) : null;
    }
    if (selection.kind === "trunk") {
      const trunk = layout.trunks.find((item) => item.id === selection.id);
      return trunk ? pointOnPolyline(trunk.points, 0.5) : null;
    }
    return null;
  }

  function selectionLabel(selection) {
    if (!selection) return "";
    if (selection.kind === "file") return model.files[selection.index].name;
    if (selection.kind === "chunk") return model.chunks[selection.index].symbol;
    if (selection.kind === "external") return model.externals[selection.index].name;
    if (selection.kind === "region") return selection.id;
    if (selection.kind === "suite") return model.suites[selection.index].label;
    if (selection.kind === "qualityOutlet") return "quality gates";
    if (selection.kind === "junction") return "synthetic junction";
    if (selection.kind === "importEdge") return "import edge";
    if (selection.kind === "externalEdge") return "boundary supply";
    if (selection.kind === "testEdge") return "test edge";
    if (selection.kind === "trunk") return "aggregated trunk";
    if (selection.kind === "bundle") return `${selection.family} region bundle`;
    return selection.kind;
  }

  function selectionSubtitle(selection) {
    if (!selection) return "";
    if (selection.kind === "file") {
      const file = model.files[selection.index];
      return `${file.type} · ${file.metrics.providers} providers · ${file.metrics.dependents} dependents · ${file.metrics.chunks} chunks`;
    }
    if (selection.kind === "chunk") {
      const chunk = model.chunks[selection.index];
      return `${chunk.kind} · ${model.files[chunk.file].path}:L${chunk.begin}-${chunk.end}`;
    }
    if (selection.kind === "external") {
      const external = model.externals[selection.index];
      return `external package · ${external.consumers.length} consumers`;
    }
    if (selection.kind === "region") {
      const region = layout.regionById.get(selection.id);
      return `${region.files.length} member files · explicit path aggregate`;
    }
    if (selection.kind === "suite") {
      const suite = model.suites[selection.index];
      return `${suite.members.length} test files · ${suite.explicitMappings} explicit mappings`;
    }
    if (selection.kind === "junction") return "layout-only · no mapped software artifact";
    if (selection.kind.includes("Edge") || selection.kind === "trunk" || selection.kind === "bundle") return "click to inspect canonical and projected direction";
    return "synthetic quality aggregate";
  }

  function sameSelection(a, b) {
    if (a == null || b == null) return a === b;
    return a.kind === b.kind && a.index === b.index && a.id === b.id && a.family === b.family;
  }

  function buildSearchRecords() {
    const records = [];
    for (const file of model.files) {
      const conceptText = file.concepts.slice(0, 32).map((index) => model.concepts[index]?.label ?? "").join(" ");
      records.push({ kind: "file", index: file.index, label: file.name, subtitle: file.path, text: `${file.path} ${file.type} ${file.roles.join(" ")} ${conceptText}`.toLowerCase() });
    }
    for (const chunk of model.chunks) {
      const file = model.files[chunk.file];
      records.push({ kind: "chunk", index: chunk.index, label: chunk.symbol, subtitle: `${file.path}:L${chunk.begin}-${chunk.end}`, text: `${chunk.symbol} ${chunk.signature ?? ""} ${chunk.kind} ${file.path}`.toLowerCase() });
    }
    for (const external of model.externals) {
      records.push({ kind: "external", index: external.index, label: external.name, subtitle: `external package${external.version ? ` · ${external.version}` : ""}`, text: `${external.name} ${external.version ?? ""} external package`.toLowerCase() });
    }
    for (const region of layout.regions) {
      records.push({ kind: "region", id: region.id, label: region.name, subtitle: `${region.files.length} files · region`, text: `${region.name} region subsystem directory`.toLowerCase() });
    }
    for (const suite of model.suites) {
      records.push({ kind: "suite", index: suite.index, label: suite.label, subtitle: `${suite.members.length} tests · ${suite.key}`, text: `${suite.key} ${suite.label} test suite`.toLowerCase() });
    }
    return records;
  }

  function updateSearchResults() {
    const query = searchInput.value.trim().toLowerCase();
    state.searchIndex = -1;
    if (!query) {
      searchResults.hidden = true;
      searchResults.innerHTML = "";
      return;
    }
    const tokens = query.split(/\s+/).filter(Boolean);
    const matches = [];
    for (const record of searchRecords) {
      if (!tokens.every((token) => record.text.includes(token))) continue;
      let score = 0;
      const label = record.label.toLowerCase();
      const subtitle = record.subtitle.toLowerCase();
      if (label === query) score += 100;
      if (label.startsWith(query)) score += 50;
      if (subtitle.startsWith(query)) score += 25;
      if (label.includes(query)) score += 15;
      score += record.kind === "file" ? 5 : record.kind === "chunk" ? 3 : 1;
      matches.push({ record, score });
    }
    matches.sort((a, b) => b.score - a.score || a.record.label.localeCompare(b.record.label));
    const top = matches.slice(0, 14).map(({ record }) => record);
    searchResults.innerHTML = top.length ? top.map((record, index) => `
      <button class="search-result" data-search-index="${index}" data-kind="${record.kind}">
        <span class="search-result-dot"></span><span><strong>${highlight(record.label, query)}</strong><small>${highlight(record.subtitle, query)}</small></span>
      </button>`).join("") : `<div style="padding:12px;color:var(--muted);font-size:10px">No artifact, symbol, package, region, or suite matches.</div>`;
    searchResults.hidden = false;
    searchResults._records = top;
  }

  function handleSearchKeyboard(event) {
    const buttons = [...searchResults.querySelectorAll(".search-result")];
    if (event.key === "ArrowDown") {
      event.preventDefault();
      state.searchIndex = Math.min(buttons.length - 1, state.searchIndex + 1);
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      state.searchIndex = Math.max(0, state.searchIndex - 1);
    } else if (event.key === "Enter" && buttons.length > 0) {
      event.preventDefault();
      chooseSearchResult(state.searchIndex < 0 ? 0 : state.searchIndex);
      return;
    } else if (event.key === "Escape") {
      searchInput.value = "";
      searchResults.hidden = true;
      searchInput.blur();
      return;
    } else {
      return;
    }
    buttons.forEach((button, index) => button.classList.toggle("active", index === state.searchIndex));
    buttons[state.searchIndex]?.scrollIntoView({ block: "nearest" });
  }

  function chooseSearchResult(index) {
    const record = searchResults._records?.[index];
    if (!record) return;
    const selection = record.kind === "region" ? { kind: "region", id: record.id } : { kind: record.kind, index: record.index };
    select(selection, { focus: true });
    searchInput.value = record.label;
    searchResults.hidden = true;
  }

  function handleKeyboard(event) {
    if (event.target.matches("input, textarea, select") && event.key !== "Escape") return;
    if (event.key === "1") setMode("combined");
    else if (event.key === "2") setMode("import");
    else if (event.key === "3") setMode("test");
    else if (event.key.toLowerCase() === "f") fitAtlas(true);
    else if (event.key === "/") {
      event.preventDefault();
      searchInput.focus();
      searchInput.select();
    } else if (event.key === "Escape") {
      setModal(false);
      searchResults.hidden = true;
      searchInput.blur();
      select(null);
    }
  }

  function setModal(open) {
    document.querySelector("#help-dialog").hidden = !open;
  }

  function exportPng() {
    const link = document.createElement("a");
    link.download = `${data.metadata.repositoryName}-cartogram-${state.mode}.png`;
    canvas.toBlob((blob) => {
      if (!blob) return;
      const url = URL.createObjectURL(blob);
      link.href = url;
      link.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    }, "image/png");
  }

  function updateAnimationLoop() {
    if (state.animationRunning || !state.layers.motion || document.hidden) return;
    state.animationRunning = true;
    const animate = (time) => {
      if (!state.layers.motion || document.hidden) {
        state.animationRunning = false;
        return;
      }
      if (time - state.lastMotionFrame > 40) {
        state.lastMotionFrame = time;
        scheduleDraw();
      }
      requestAnimationFrame(animate);
    };
    requestAnimationFrame(animate);
  }

  function formatNumber(value) {
    return new Intl.NumberFormat("en", { notation: value >= 10000 ? "compact" : "standard", maximumFractionDigits: 1 }).format(value);
  }

  function formatBytes(bytes) {
    if (!Number.isFinite(bytes) || bytes <= 0) return "0 B";
    const units = ["B", "KB", "MB", "GB"];
    const index = Math.min(units.length - 1, Math.floor(Math.log(bytes) / Math.log(1024)));
    return `${(bytes / 1024 ** index).toFixed(index === 0 ? 0 : 1)} ${units[index]}`;
  }

  function colorWithAlpha(color, alpha) {
    const parsed = d3.color(color);
    if (!parsed) return color;
    parsed.opacity = alpha;
    return parsed.formatRgb();
  }

  function escapeHtml(value) {
    return String(value ?? "").replace(/[&<>'"]/g, (character) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" }[character]));
  }

  function highlight(value, query) {
    const escaped = escapeHtml(value);
    if (!query || query.includes(" ")) return escaped;
    const safeQuery = query.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    return escaped.replace(new RegExp(`(${safeQuery})`, "ig"), "<mark style='background:rgba(85,199,255,.18);color:inherit;border-radius:2px'>$1</mark>");
  }
})();
