(function attachAtlasModel(global) {
  "use strict";

  const ROOT_REGION = "(root)";

  function stableNumber(text) {
    let hash = 2166136261;
    const value = String(text);
    for (let index = 0; index < value.length; index += 1) {
      hash ^= value.charCodeAt(index);
      hash = Math.imul(hash, 16777619);
    }
    return hash >>> 0;
  }

  function compareStableFiles(a, b) {
    return a.path.localeCompare(b.path, "en") || a.id.localeCompare(b.id, "en");
  }

  function hasRole(file, role) {
    return Array.isArray(file.roles) && file.roles.includes(role);
  }

  function suiteKey(file) {
    const parts = file.path.split("/").filter(Boolean);
    if (parts[0] === "tests") {
      if (parts[1] === "test_tutorial" && parts.length >= 3) {
        return parts.slice(0, 3).join("/");
      }
      return parts.slice(0, Math.min(2, parts.length - 1 || 1)).join("/") || "tests";
    }
    if (parts[0] === "docs_src" && hasRole(file, "test")) {
      return parts.slice(0, Math.min(2, parts.length - 1 || 1)).join("/") || "docs_src tests";
    }
    return file.directory === ROOT_REGION ? "root tests" : file.directory;
  }

  function computeTransitiveDependents(consumersByProvider) {
    const count = consumersByProvider.length;
    const result = new Uint32Array(count);
    const marks = new Uint32Array(count);
    let token = 0;
    const stack = [];

    for (let start = 0; start < count; start += 1) {
      token += 1;
      if (token === 0xffffffff) {
        marks.fill(0);
        token = 1;
      }
      stack.length = 0;
      stack.push(start);
      marks[start] = token;
      let total = 0;
      while (stack.length > 0) {
        const current = stack.pop();
        for (const next of consumersByProvider[current]) {
          if (marks[next] === token) continue;
          marks[next] = token;
          total += 1;
          stack.push(next);
        }
      }
      result[start] = total;
    }
    return result;
  }

  function choosePrimaryParents(files, providersByConsumer, consumersByProvider, supportScore) {
    const parent = new Int32Array(files.length);
    parent.fill(-1);
    for (let consumer = 0; consumer < files.length; consumer += 1) {
      const candidates = providersByConsumer[consumer];
      if (candidates.length === 0) continue;
      let best = candidates[0];
      for (let index = 1; index < candidates.length; index += 1) {
        const candidate = candidates[index];
        const candidateTuple = [
          supportScore[candidate],
          consumersByProvider[candidate].length,
          -providersByConsumer[candidate].length,
        ];
        const bestTuple = [
          supportScore[best],
          consumersByProvider[best].length,
          -providersByConsumer[best].length,
        ];
        let replace = false;
        for (let part = 0; part < candidateTuple.length; part += 1) {
          if (candidateTuple[part] > bestTuple[part]) {
            replace = true;
            break;
          }
          if (candidateTuple[part] < bestTuple[part]) break;
          if (part === candidateTuple.length - 1 && compareStableFiles(files[candidate], files[best]) > 0) {
            replace = true;
          }
        }
        if (replace) best = candidate;
      }
      parent[consumer] = best;
    }
    return parent;
  }

  function findParentCycles(parent) {
    const state = new Uint8Array(parent.length);
    const cycles = [];
    for (let start = 0; start < parent.length; start += 1) {
      if (state[start] !== 0) continue;
      const path = [];
      const localIndex = new Map();
      let current = start;
      while (current >= 0 && state[current] === 0 && !localIndex.has(current)) {
        localIndex.set(current, path.length);
        path.push(current);
        current = parent[current];
      }
      if (current >= 0 && localIndex.has(current)) {
        cycles.push(path.slice(localIndex.get(current)));
      }
      for (const node of path) state[node] = 2;
    }
    return cycles;
  }

  function breakParentCycles(parent, files, supportScore, consumersByProvider) {
    const removed = [];
    while (true) {
      const cycles = findParentCycles(parent);
      if (cycles.length === 0) break;
      for (const cycle of cycles) {
        let cut = cycle[0];
        for (const candidate of cycle.slice(1)) {
          const scoreDelta = supportScore[candidate] - supportScore[cut];
          if (scoreDelta > 1e-12) {
            cut = candidate;
          } else if (Math.abs(scoreDelta) <= 1e-12) {
            const degreeDelta = consumersByProvider[candidate].length - consumersByProvider[cut].length;
            if (degreeDelta > 0 || (degreeDelta === 0 && compareStableFiles(files[candidate], files[cut]) > 0)) {
              cut = candidate;
            }
          }
        }
        removed.push([cut, parent[cut]]);
        parent[cut] = -1;
      }
    }
    return removed;
  }

  function computeForest(files, parent, importActive) {
    const children = Array.from({ length: files.length }, () => []);
    for (let child = 0; child < parent.length; child += 1) {
      const provider = parent[child];
      if (provider >= 0) children[provider].push(child);
    }
    for (const list of children) list.sort((a, b) => compareStableFiles(files[a], files[b]));

    const roots = [];
    for (let index = 0; index < files.length; index += 1) {
      if (importActive[index] && parent[index] < 0) roots.push(index);
    }
    roots.sort((a, b) => compareStableFiles(files[a], files[b]));

    const demand = new Float64Array(files.length);
    const order = [];
    const stack = roots.map((node) => [node, false]);
    while (stack.length > 0) {
      const [node, visited] = stack.pop();
      if (visited) {
        order.push(node);
        continue;
      }
      stack.push([node, true]);
      for (const child of children[node]) stack.push([child, false]);
    }
    for (const node of order) {
      const kids = children[node];
      demand[node] = kids.length === 0 ? 1 : kids.reduce((sum, child) => sum + demand[child], 0);
    }
    return { children, roots, demand };
  }

  function groupBundles(edges, sourceRegion, targetRegion) {
    const groups = new Map();
    for (const edge of edges) {
      const source = sourceRegion(edge);
      const target = targetRegion(edge);
      const key = `${source}\u0000${target}`;
      let group = groups.get(key);
      if (!group) {
        group = { id: key, sourceRegion: source, targetRegion: target, count: 0, demand: 0, edgeIds: [] };
        groups.set(key, group);
      }
      group.count += 1;
      group.demand += edge.demand ?? 1;
      group.edgeIds.push(edge.id);
    }
    return [...groups.values()].sort((a, b) => b.count - a.count || a.id.localeCompare(b.id));
  }

  function buildDirectoryTree(files, fileValue) {
    const root = { id: "dir:/", name: "repository", kind: "directory", path: "", children: [], childMap: new Map() };
    for (const file of files) {
      const parts = file.path.split("/").filter(Boolean);
      let current = root;
      const directoryParts = parts.slice(0, -1);
      if (directoryParts.length === 0) {
        const part = ROOT_REGION;
        let child = current.childMap.get(part);
        if (!child) {
          child = { id: `dir:${ROOT_REGION}`, name: ROOT_REGION, path: ROOT_REGION, kind: "directory", children: [], childMap: new Map() };
          current.childMap.set(part, child);
          current.children.push(child);
        }
        current = child;
      }
      for (const part of directoryParts) {
        let child = current.childMap.get(part);
        if (!child) {
          const childPath = current.path ? `${current.path}/${part}` : part;
          child = { id: `dir:${childPath}`, name: part, path: childPath, kind: "directory", children: [], childMap: new Map() };
          current.childMap.set(part, child);
          current.children.push(child);
        }
        current = child;
      }
      current.children.push({
        id: `file:${file.index}`,
        name: file.name,
        path: file.path,
        kind: "file",
        file: file.index,
        value: fileValue(file),
      });
    }

    function clean(node) {
      if (!node.children) return node;
      node.children.sort((a, b) => {
        if (a.kind !== b.kind) return a.kind === "directory" ? -1 : 1;
        return a.path.localeCompare(b.path, "en");
      });
      for (const child of node.children) clean(child);
      delete node.childMap;
      return node;
    }
    return clean(root);
  }

  function derive(data) {
    const files = data.files.map((file, index) => ({ ...file, index }));
    const externals = data.externals.map((item, index) => ({ ...item, index }));
    const fileCount = files.length;

    const providersByConsumer = Array.from({ length: fileCount }, () => []);
    const consumersByProvider = Array.from({ length: fileCount }, () => []);
    const externalByConsumer = Array.from({ length: fileCount }, () => []);
    const consumersByExternal = Array.from({ length: externals.length }, () => []);
    const subjectsByTest = Array.from({ length: fileCount }, () => []);
    const testsBySubject = Array.from({ length: fileCount }, () => []);

    for (const [consumer, provider] of data.relations.imports) {
      providersByConsumer[consumer].push(provider);
      consumersByProvider[provider].push(consumer);
    }
    for (const [consumer, external] of data.relations.externalImports) {
      externalByConsumer[consumer].push(external);
      consumersByExternal[external].push(consumer);
    }
    for (const [test, subject] of data.relations.tests) {
      subjectsByTest[test].push(subject);
      testsBySubject[subject].push(test);
    }

    const transitiveDependents = computeTransitiveDependents(consumersByProvider);
    const supportScore = new Float64Array(fileCount);
    const importActive = new Uint8Array(fileCount);
    for (const file of files) {
      const index = file.index;
      const testPenalty = hasRole(file, "test") ? 0.35 : 0;
      supportScore[index] =
        1 +
        2 * Math.log1p(transitiveDependents[index]) +
        Math.log1p(consumersByProvider[index].length) +
        0.25 * Math.log1p(providersByConsumer[index].length) -
        testPenalty;
      importActive[index] = Number(
        providersByConsumer[index].length > 0 ||
        consumersByProvider[index].length > 0 ||
        externalByConsumer[index].length > 0,
      );
    }

    const parent = choosePrimaryParents(files, providersByConsumer, consumersByProvider, supportScore);
    const cycleCuts = breakParentCycles(parent, files, supportScore, consumersByProvider);
    const forest = computeForest(files, parent, importActive);

    const primaryKey = new Set();
    for (let consumer = 0; consumer < parent.length; consumer += 1) {
      if (parent[consumer] >= 0) primaryKey.add(`${consumer}:${parent[consumer]}`);
    }

    const importEdges = data.relations.imports.map(([consumer, provider], index) => ({
      id: `import:${index}`,
      relation: "imports",
      canonicalSource: consumer,
      canonicalTarget: provider,
      source: provider,
      target: consumer,
      directionTransform: "reverse",
      primary: primaryKey.has(`${consumer}:${provider}`),
      cycleCut: cycleCuts.some(([cutConsumer, cutProvider]) => cutConsumer === consumer && cutProvider === provider),
      demand: forest.demand[consumer] || 1,
    }));

    const externalEdges = data.relations.externalImports.map(([consumer, external], index) => ({
      id: `external-import:${index}`,
      relation: "importsExternal",
      canonicalSource: consumer,
      canonicalTarget: external,
      sourceExternal: external,
      target: consumer,
      directionTransform: "reverse",
      demand: Math.max(1, forest.demand[consumer] || 1),
    }));

    const testEdges = data.relations.tests.map(([test, subject], index) => ({
      id: `tests:${index}`,
      relation: "tests",
      canonicalSource: test,
      canonicalTarget: subject,
      source: subject,
      target: test,
      directionTransform: "reverse",
      demand: 1,
    }));

    const regionMap = new Map();
    for (const file of files) {
      let region = regionMap.get(file.region);
      if (!region) {
        region = { id: file.region, name: file.region, files: [], production: 0, tests: 0, gates: 0, importsIn: 0, importsOut: 0 };
        regionMap.set(file.region, region);
      }
      region.files.push(file.index);
      if (hasRole(file, "production")) region.production += 1;
      if (hasRole(file, "test")) region.tests += 1;
      if (hasRole(file, "quality_gate")) region.gates += 1;
    }
    const regions = [...regionMap.values()].sort((a, b) => b.files.length - a.files.length || a.id.localeCompare(b.id));

    for (const edge of importEdges) {
      const sourceRegion = files[edge.source].region;
      const targetRegion = files[edge.target].region;
      regionMap.get(sourceRegion).importsOut += 1;
      regionMap.get(targetRegion).importsIn += 1;
    }

    const suitesByKey = new Map();
    for (const file of files) {
      if (!hasRole(file, "test")) continue;
      const key = suiteKey(file);
      let suite = suitesByKey.get(key);
      if (!suite) {
        suite = { id: `suite:${key}`, key, label: key.split("/").at(-1) || key, members: [], explicitMappings: 0 };
        suitesByKey.set(key, suite);
      }
      suite.members.push(file.index);
      suite.explicitMappings += subjectsByTest[file.index].length;
    }
    const suites = [...suitesByKey.values()].sort((a, b) => b.members.length - a.members.length || a.key.localeCompare(b.key));
    const suiteByFile = new Int32Array(fileCount);
    suiteByFile.fill(-1);
    suites.forEach((suite, suiteIndex) => {
      suite.index = suiteIndex;
      suite.members.sort((a, b) => compareStableFiles(files[a], files[b]));
      for (const file of suite.members) suiteByFile[file] = suiteIndex;
    });

    const testFiles = files.filter((file) => hasRole(file, "test")).map((file) => file.index);
    const qualityGates = files.filter((file) => hasRole(file, "quality_gate")).map((file) => file.index);
    const unmappedTestFiles = testFiles.filter((index) => subjectsByTest[index].length === 0);
    const undrainedProductionFiles = files
      .filter((file) => hasRole(file, "production") && testsBySubject[file.index].length === 0)
      .map((file) => file.index);

    const importRegionBundles = groupBundles(
      importEdges,
      (edge) => files[edge.source].region,
      (edge) => files[edge.target].region,
    );
    const externalRegionBundles = groupBundles(
      externalEdges,
      () => "@external",
      (edge) => files[edge.target].region,
    );
    const testRegionBundles = groupBundles(
      testEdges,
      (edge) => files[edge.source].region,
      (edge) => files[edge.target].region,
    );

    const fileValue = (file) => {
      const chunks = file.chunks?.length ?? 0;
      const sizeComponent = Math.min(4, Math.log1p(file.size || 0) / 3.2);
      const chunkComponent = Math.min(4, Math.sqrt(chunks) * 0.42);
      const supportComponent = Math.min(5, supportScore[file.index] * 0.34);
      const qualityComponent = hasRole(file, "test") || hasRole(file, "quality_gate") ? 0.7 : 0;
      let roleWeight = 0.55;
      if (hasRole(file, "production")) roleWeight = 2.15;
      else if (hasRole(file, "test")) roleWeight = 0.72;
      else if (hasRole(file, "documentation")) roleWeight = 0.16;
      else if (hasRole(file, "asset")) roleWeight = 0.12;
      else if (hasRole(file, "quality_gate")) roleWeight = 0.85;
      else if (hasRole(file, "configuration")) roleWeight = 0.48;
      return Math.max(0.36, (1 + sizeComponent + chunkComponent + supportComponent + qualityComponent) * roleWeight);
    };

    const directoryTree = buildDirectoryTree(files, fileValue);

    for (const file of files) {
      const index = file.index;
      file.metrics = {
        providers: providersByConsumer[index].length,
        dependents: consumersByProvider[index].length,
        transitiveDependents: transitiveDependents[index],
        externalProviders: externalByConsumer[index].length,
        tests: subjectsByTest[index].length,
        testedBy: testsBySubject[index].length,
        chunks: file.chunks?.length ?? 0,
        supportScore: supportScore[index],
        subtreeDemand: forest.demand[index],
      };
      file.primaryProvider = parent[index];
      file.suite = suiteByFile[index];
      file.seed = stableNumber(file.id);
    }

    for (const external of externals) {
      external.consumers = consumersByExternal[external.index];
      external.seed = stableNumber(external.id);
    }

    return {
      data,
      files,
      externals,
      concepts: data.concepts,
      chunks: data.chunks,
      providersByConsumer,
      consumersByProvider,
      externalByConsumer,
      consumersByExternal,
      subjectsByTest,
      testsBySubject,
      transitiveDependents,
      supportScore,
      importActive,
      parent,
      cycleCuts,
      children: forest.children,
      roots: forest.roots,
      subtreeDemand: forest.demand,
      importEdges,
      externalEdges,
      testEdges,
      regions,
      regionMap,
      suites,
      suiteByFile,
      testFiles,
      qualityGates,
      unmappedTestFiles,
      undrainedProductionFiles,
      importRegionBundles,
      externalRegionBundles,
      testRegionBundles,
      directoryTree,
      stableNumber,
      hasRole,
    };
  }

  global.AtlasModel = Object.freeze({
    derive,
    stableNumber,
    suiteKey,
    findParentCycles,
    breakParentCycles,
  });
})(typeof window !== "undefined" ? window : globalThis);
