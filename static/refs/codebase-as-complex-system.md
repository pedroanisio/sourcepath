---
title: "Codebase as a Complex System — The Vascular-Graph Reference Model"
document_id: VGR-HR-001
version: 1.1
date: "2026-07-10"
status: >-
  Formal specification of a human vascular replica, used in this repository as a
  structural reference model for treating a codebase as an attributed, directed
  multigraph. See §0 for the codebase analogy and its epistemic boundaries.
generated_by: >-
  Base VGR-HR-001 vascular specification: prior authorship, not independently
  verified here. Codebase-analogy layer (§0), reference verification, and math
  normalisation: Claude Fable 5 via Claude Code.
disclaimer:
  notice: >-
    No information within this document should be taken for granted. Any statement
    or premise not backed by a real logical definition or verifiable reference may
    be invalid, erroneous, or a hallucination. The vascular and haemodynamic claims
    require domain-expert verification; the codebase analogy in §0 is an interpretive
    lens, not a proven isomorphism (see PALS's Law and CLAUDE.md Rule 2).
  generated_by: "Claude Fable 5 via Claude Code"
  date: "2026-07-10"
---

# Multiscale Human Vascular Graph and Haemodynamic Replica Specification

**Document ID:** VGR-HR-001
**Version:** 1.1
**Status:** Formal Specification · used in this repository as a codebase reference model (see §0)

---

## 0. Codebase Analogy — How to Read This Document in This Repository

**Status of this section: interpretive, non-normative.** Sections §1–§19 are a formal specification of a *biological* vascular replica. This repository — `codebase-mapper` — has no vessels and no blood; it maps *source repositories* into attributed directed graphs. This section states why a vascular specification earns a place in `static/refs/`, and it draws the line explicitly between the parts of the model that transfer to a codebase and the parts that are only metaphor.

The thesis is narrow and defensible: **a codebase and a vascular system are both large, cyclic, multiscale, attributed directed graphs recovered from a noisy source by a pipeline that must never confuse what it observed with what it inferred.** Everything this document says about *graph structure, multiscale decomposition, reconstruction-and-repair, graph metrics, and provenance discipline* transfers, because those are statements about that shared abstraction, not about biology. Everything it says about *fluid, pressure, elasticity, and wave propagation* is the physics of a continuum medium that a codebase does not possess; importing it quantitatively would be exactly the kind of unbacked claim PALS's Law (see `CLAUDE.md`) forbids.

### 0.1 Load-bearing correspondences (use these)

These transfer because they describe the shared graph abstraction, not the biology.

| Vascular spec | Codebase-mapper analogue | Why it holds |
| --- | --- | --- |
| §5 Directed cyclic multigraph; node/edge classes; cycles first-class (`TOP-005`, `TOP-009`) | File / module / symbol nodes; import, call, and dependency edges | Both are directed multigraphs with real cycles — collateral loops ↔ circular dependencies — and neither is a pure tree. |
| §6 Node/edge records carry `provenance` and `uncertainty` | `cbm` node/edge records with provenance fields | The record shape is identical: every element states where it came from and how certain it is. |
| §13 Multiscale decomposition; declared resolution boundary (`MSL-001`) | repo → package → module → function; explicit graph vs. summarised regions | Both must *declare* where explicit representation stops and summarisation begins. |
| §14 Image → segmentation → skeleton → raw graph → repaired graph (`REC-011`, `REC-012`) | source → tokens/AST → raw graph → validated bundle | Same pipeline shape: derive a graph from a noisy artifact, then repair it while keeping an audit trail. |
| §16 Degree, Strahler order, weighted shortest path, cycle rank $\beta_1$, betweenness | The same metrics computed on the code graph | Graph theory is domain-agnostic. $\beta_1 = |E| - |V| + c$ counts independent circular-dependency loops directly. |
| §17 / `VAL-018` Distinguish measured / segmented / fitted / inferred / generated; `REC-012` observed vs. inferred; `SYN-001` label generated elements | `cbm`'s central rule: separate mechanically-derived facts, generated inferences, and LLM-authored enrichment | The **strongest** correspondence — it is this repository's reason to exist and a restatement of PALS's Law. |
| §17.1 Declared tolerances + validation residuals | Contract and drift verifiers under `tests/` | Validation is a first-class artifact in both, not post-hoc. |

### 0.2 Metaphorical correspondences (read, do not import)

Worth reading as provocations — *what would this even mean for code?* — but they carry no quantitative claim about a codebase, and none may be cited as if they did.

| Vascular spec | Why it is only metaphor for a codebase |
| --- | --- |
| §8 Steady haemodynamics — Poiseuille resistance, flow conservation $B\mathbf q = \mathbf s$, hydraulic Laplacian | A codebase has no conserved fluid. Calls and data do not obey a Kirchhoff balance at a node, and "resistance" has no measured analogue. |
| §11–§12 Pulsatile flow, wall elasticity, wave speed, impedance, reflection | Static code has no time-domain wave; these need a continuum medium and a clock the graph does not have. |
| §7 Geometry — centreline, curvature, tortuosity, surface mesh | There is no metric embedding of code. Editor layout is not geometry; distances are not physical. |
| §9–§10 Murray's law $r_p^{\gamma_M} = \sum r_d^{\gamma_M}$, junction optimisation | Suggestive of branching/fan-out cost laws, but no exponent has been derived or measured for software. Treat as a question, not a result. |
| §15 Synthetic completion — the *optimisation* objective (demand points, material/dissipation cost) | The construction physics does not apply. *Exception:* the `SYN-001` discipline — every generated element is labelled as generated — is load-bearing and appears in §0.1. |

### 0.3 How to use this document here

Read §5–§6, §13–§14, and §16–§18 as a rigorous checklist for what `codebase-mapper` bundles should contain and guarantee: unique identifiers, valid endpoints, declared resolution boundaries, an auditable reconstruction/repair trail, standard graph metrics, and — above all — per-element provenance that never lets an inference be mistaken for an observation. Read §7–§12 and the §15 optimisation as an extended metaphor that sharpens intuition about branching and bottlenecks without licensing any numeric transfer. When in doubt, the boundary is the one PALS's Law draws: a mechanically derived graph fact is evidence; an analogy to blood flow is interpretation, and must be labelled as such.

---

## 1. Purpose

This specification defines the topology, geometry, haemodynamics, multiscale representation, reconstruction procedures, data model, and validation requirements for a computational replica of the human vascular system.

A conforming vascular replica SHALL represent the vascular system as an attributed, directed graph coupled to geometric, hydraulic, wave-propagation, and tissue-supply models.

This specification defines computational and numerical conformance. Conformance does not, by itself, establish clinical validity, diagnostic suitability, or regulatory approval.

---

## 2. Normative Language

The terms **MUST**, **MUST NOT**, **REQUIRED**, **SHALL**, and **SHALL NOT** indicate mandatory requirements.

The terms **SHOULD**, **SHOULD NOT**, and **RECOMMENDED** indicate requirements that may be departed from only when the reason is documented.

The terms **MAY** and **OPTIONAL** indicate permitted implementation choices.

---

## 3. Conformance Profiles

An implementation SHALL declare one or more of the following conformance profiles.

| Profile | Name                          | Scope                                                                       |
| ------- | ----------------------------- | --------------------------------------------------------------------------- |
| VG-1    | Vascular Graph and Geometry   | Topology, geometric representation, attributes, and structural validation   |
| SH-1    | Steady Haemodynamics          | VG-1 plus steady pressure and flow simulation                               |
| PH-1    | Pulsatile Haemodynamics       | VG-1 plus one-dimensional compliant-vessel simulation                       |
| MS-1    | Multiscale Vascular Replica   | Explicit vessels, effective microvascular beds, and tissue coupling         |
| IR-1    | Image-Derived Reconstruction  | Reconstruction from CT, MRI, microscopy, angiography, or equivalent imaging |
| SG-1    | Synthetic Vascular Generation | Procedural or optimisation-based generation of unresolved vessels           |

SH-1, PH-1, MS-1, IR-1, and SG-1 SHALL include VG-1 conformance.

An implementation claiming multiple profiles SHALL satisfy the union of their requirements.

---

## 4. System Definition

### 4.1 Formal Model

A conforming computational vascular replica SHALL be represented as

$$
\boxed{
\mathcal V=
\left(
G,\mathcal X,\mathcal H,\mathcal T,\mathcal B,\mathcal U
\right)
}
$$

where:

$$
\begin{aligned}
G &= \text{vascular graph topology},\\
\mathcal X &= \text{three-dimensional vessel geometry},\\
\mathcal H &= \text{haemodynamic model and state},\\
\mathcal T &= \text{tissue supply, drainage, and exchange model},\\
\mathcal B &= \text{boundary and initial conditions},\\
\mathcal U &= \text{provenance, uncertainty, and validation metadata}.
\end{aligned}
$$

### 4.2 Units

**SYS-001.** A conforming implementation MUST use a declared unit system.

**SYS-002.** The default internal unit system SHALL be SI:

| Quantity                                 | Unit                                           |
| ---------------------------------------- | ---------------------------------------------- |
| Position, length, radius, wall thickness | metre, $\mathrm m$                             |
| Area                                     | square metre, $\mathrm m^2$                    |
| Volume                                   | cubic metre, $\mathrm m^3$                     |
| Time                                     | second, $\mathrm s$                            |
| Pressure                                 | pascal, $\mathrm{Pa}$                          |
| Volumetric flow                          | cubic metre per second, $\mathrm{m^3\,s^{-1}}$  |
| Dynamic viscosity                        | pascal-second, $\mathrm{Pa\,s}$                 |
| Density                                  | kilogram per cubic metre, $\mathrm{kg\,m^{-3}}$ |
| Young’s modulus                          | pascal, $\mathrm{Pa}$                          |
| Hydraulic resistance                     | $\mathrm{Pa\,s\,m^{-3}}$                         |
| Compliance                               | $\mathrm{m^3,Pa^{-1}}$                         |
| Wave speed                               | metre per second, $\mathrm{m\,s^{-1}}$          |
| Characteristic impedance                 | $\mathrm{Pa\,s\,m^{-3}}$                         |

**SYS-003.** Alternative storage units, including millimetres and millimetres of mercury, MAY be used when every field is unambiguously labelled and conversion to SI is defined.

### 4.3 Coordinate System

**SYS-004.** All geometric entities MUST be expressed in a declared three-dimensional world coordinate system.

**SYS-005.** Image-derived models MUST preserve the transformation between voxel indices and world coordinates.

**SYS-006.** All centreline, surface, tissue, and boundary-condition coordinates MUST use the same frame or include an explicit transformation into that frame.

---

## 5. Vascular Graph Topology

### 5.1 Graph Definition

The vascular topology SHALL be represented as a directed multigraph

$$
G=(V,E,\sigma,\tau),
$$

where:

$$
\sigma:E\rightarrow V
$$

maps each edge to its reference source node and

$$
\tau:E\rightarrow V
$$

maps each edge to its reference target node.

The edge orientation defines a reference direction. Physical flow MAY be negative relative to that orientation.

### 5.2 Node Classes

Each node $v\in V$ SHALL have exactly one primary node class.

| Node class          | Meaning                                                  |
| ------------------- | -------------------------------------------------------- |
| `heart_chamber`     | Cardiac chamber or equivalent lumped cardiac compartment |
| `major_inlet`       | Prescribed inflow or upstream pressure boundary          |
| `major_outlet`      | Prescribed outflow or downstream pressure boundary       |
| `bifurcation`       | One principal inflow with multiple outflows              |
| `convergence`       | Multiple inflows with one principal outflow              |
| `junction`          | General multi-edge connection                            |
| `anastomosis`       | Connection supporting collateral or alternative pathways |
| `terminal_arterial` | Terminal point of an explicit arterial subtree           |
| `exchange_bed`      | Capillary, porous, or lumped tissue-exchange compartment |
| `terminal_venous`   | Origin of an explicit venous subtree                     |
| `portal_interface`  | Interface between serial capillary or organ systems      |
| `lumped_component`  | Windkessel, valve, pump, shunt, or equivalent element    |

**TOP-001.** Every node MUST have a unique identifier and a three-dimensional position unless the node represents a purely lumped component.

**TOP-002.** A purely lumped node without a physical position MUST be explicitly marked.

### 5.3 Edge Classes

Each edge $e\in E$ SHALL be assigned one of the following primary classes:

$$
{
\text{arterial},
\text{arteriolar},
\text{capillary},
\text{venular},
\text{venous},
\text{pulmonary},
\text{portal},
\text{collateral},
\text{shunt},
\text{lumped}
}.
$$

**TOP-003.** Every edge MUST reference valid source and target node identifiers.

**TOP-004.** Parallel edges between the same node pair SHALL be permitted.

**TOP-005.** Independent cycles SHALL be permitted to represent anastomoses, collateral pathways, pulmonary and systemic loops, and portal systems.

**TOP-006.** Self-loop edges SHALL NOT be used for geometric vessels. A self-loop MAY represent a declared lumped element.

### 5.4 Network Architecture

The complete vascular graph SHOULD be decomposable as

$$
G=
G_A\cup G_C\cup G_V\cup G_H,
$$

where:

$$
\begin{aligned}
G_A &= \text{arterial network},\\
G_C &= \text{capillary or effective exchange network},\\
G_V &= \text{venous network},\\
G_H &= \text{heart and cardiopulmonary coupling}.
\end{aligned}
$$

**TOP-007.** A whole-system model MUST distinguish the systemic and pulmonary circuits.

**TOP-008.** Portal systems, shunts, and collateral pathways MUST be represented explicitly when they are within the declared anatomical scope.

**TOP-009.** A complete model SHALL be treated as a directed cyclic multigraph rather than as a pure binary tree.

---

## 6. Required Data Model

### 6.1 Node Record

Each node record SHALL contain the following fields.

| Field                 | Requirement                                                            |
| --------------------- | ---------------------------------------------------------------------- |
| `node_id`             | REQUIRED unique identifier                                             |
| `node_class`          | REQUIRED node classification                                           |
| `position`            | REQUIRED three-dimensional coordinate unless purely lumped             |
| `circuit`             | REQUIRED systemic, pulmonary, portal, cardiac, or local classification |
| `boundary_type`       | REQUIRED; `none`, `pressure`, `flow`, `impedance`, `pump`, or `mixed`  |
| `boundary_parameters` | REQUIRED when `boundary_type` is not `none`                            |
| `tissue_region_ids`   | REQUIRED list, which MAY be empty                                      |
| `provenance`          | REQUIRED source or derivation record                                   |
| `uncertainty`         | REQUIRED uncertainty value or explicit `not_available` designation     |

### 6.2 Edge Record

Each geometric edge record SHALL contain the following fields.

| Field                    | Requirement                                             |
| ------------------------ | ------------------------------------------------------- |
| `edge_id`                | REQUIRED unique identifier                              |
| `source_node_id`         | REQUIRED                                                |
| `target_node_id`         | REQUIRED                                                |
| `edge_class`             | REQUIRED                                                |
| `centreline`             | REQUIRED ordered three-dimensional curve                |
| `radius_profile`         | REQUIRED positive radius function or samples            |
| `reference_area_profile` | REQUIRED or derivable from radius                       |
| `length`                 | REQUIRED or deterministically derivable                 |
| `viscosity_model`        | REQUIRED for SH-1 or PH-1                               |
| `density`                | REQUIRED for PH-1                                       |
| `wall_thickness`         | REQUIRED for compliant-vessel PH-1 models               |
| `youngs_modulus`         | REQUIRED for material-based PH-1 models                 |
| `poisson_ratio`          | REQUIRED when used by the wall law                      |
| `compliance`             | REQUIRED when represented as a lumped compliant segment |
| `hydraulic_resistance`   | REQUIRED or derivable for SH-1                          |
| `branch_order`           | RECOMMENDED                                             |
| `anatomical_label`       | RECOMMENDED                                             |
| `provenance`             | REQUIRED                                                |
| `uncertainty`            | REQUIRED or explicitly unavailable                      |

### 6.3 Dynamic State

A haemodynamic state record SHALL identify its simulation time and SHALL contain the variables required by the claimed profile.

For SH-1, the state SHALL include:

$$
\mathbf p={p_v}_{v\in V}
$$

and

$$
\mathbf q={q_e}_{e\in E}.
$$

For PH-1, the state SHALL include, for every simulated vessel:

$$
A_e(x,t),\qquad Q_e(x,t),\qquad P_e(x,t).
$$

Velocity MAY be stored explicitly or derived as

$$
u_e(x,t)=\frac{Q_e(x,t)}{A_e(x,t)}.
$$

---

## 7. Geometric Vessel Model

### 7.1 Centreline

Each geometric vessel edge SHALL be represented by a continuous parametric centreline

$$
\mathbf c_e(\ell)=
\begin{bmatrix}
x_e(\ell)\\
y_e(\ell)\\
z_e(\ell)
\end{bmatrix},
\qquad
\ell\in[0,L_e].
$$

The preferred parameter is arc length, satisfying

$$
\left|
\frac{\partial\mathbf c_e}{\partial \ell}
\right|=1.
$$

For a non-arc-length parameter $s\in[0,1]$, vessel length SHALL be computed as

$$
L_e=
\int_0^1
\left|
\frac{d\mathbf c_e}{ds}
\right|,ds.
$$

**GEO-001.** Every geometric edge MUST have (L_e>0).

**GEO-002.** The centreline endpoints MUST coincide with the source and target node positions within the declared endpoint tolerance.

### 7.2 Radius and Area

The reference radius SHALL be represented as a positive function

$$
r_{0,e}(\ell)>0.
$$

For a circular cross-section,

$$
A_{0,e}(\ell)=\pi r_{0,e}(\ell)^2.
$$

**GEO-003.** Radius values MUST remain positive over the complete centreline domain.

**GEO-004.** A non-circular cross-section MAY be used, but its area, perimeter, orientation, and hydraulic-equivalence method MUST be declared.

### 7.3 Vessel Surface

For a circular vessel, the lumen surface MAY be constructed as

$$
\mathbf S_e(\ell,\theta)
=
\mathbf c_e(\ell)
+
r_e(\ell)
\left[
\mathbf n_e(\ell)\cos\theta+
\mathbf b_e(\ell)\sin\theta
\right],
$$

where

$$
\theta\in[0,2\pi)
$$

and $\mathbf n_e,\mathbf b_e$ form an orthonormal frame perpendicular to the centreline tangent.

**GEO-005.** A rotation-minimising frame SHOULD be used for surface generation.

**GEO-006.** A classical Frenet frame SHALL NOT be used without special handling at points where curvature approaches zero.

### 7.4 Curvature and Tortuosity

For a general centreline parameter (s), curvature SHALL be defined as

$$
\kappa_e(s)=
\frac{
\left|
\mathbf c'_e(s)\times\mathbf c''_e(s)
\right|
}{
\left|
\mathbf c'_e(s)
\right|^3
}.
$$

Geometric tortuosity SHALL be defined as

$$
\tau_e=
\frac{L_e}{
\left|
\mathbf c_e(L_e)-\mathbf c_e(0)
\right|
}.
$$

A straight segment has

$$
\tau_e=1.
$$

**GEO-007.** An implementation MUST define its treatment of tortuosity when the endpoint displacement is zero or below numerical tolerance.

### 7.5 Surface and Volume Meshes

When a surface or volume mesh is generated:

**GEO-008.** The mesh MUST preserve the graph’s inlet, outlet, and junction identities.

**GEO-009.** A closed lumen surface intended for computational fluid dynamics MUST be watertight except at declared boundary openings.

**GEO-010.** Non-manifold edges, inverted elements, and unresolved self-intersections MUST be reported.

**GEO-011.** Mesh elements SHOULD retain a mapping to their originating graph edge or node.

---

## 8. Steady Haemodynamic Model

### 8.1 Edge Resistance

For steady, laminar flow through a rigid circular segment of constant radius,

$$
Q_e=
\frac{\pi r_e^4}{8\mu_eL_e}\Delta P_e.
$$

The corresponding hydraulic resistance is

$$
\boxed{
R_e=
\frac{8\mu_eL_e}{\pi r_e^4}
}
$$

and

$$
\Delta P_e=R_eQ_e.
$$

For spatially varying radius or viscosity, the segment resistance SHOULD be computed as

$$
\boxed{
R_e=
\int_0^{L_e}
\frac{8\mu_e(\ell)}
{\pi r_e(\ell)^4}
\,d\ell
}
$$

under the local fully developed circular-flow approximation.

**HYD-001.** Every SH-1 edge MUST have a positive finite resistance or a declared non-resistive constitutive model.

**HYD-002.** The viscosity model MUST state whether viscosity is constant, diameter-dependent, shear-dependent, haematocrit-dependent, or externally prescribed.

**HYD-003.** Use of Poiseuille resistance MUST be documented as an idealised constitutive assumption.

### 8.2 Incidence Matrix

Define the oriented incidence matrix

$$
B\in{-1,0,1}^{|V|\times|E|}
$$

by

$$
B_{ve}=
\begin{cases}
+1,&v=\sigma(e),\\
-1,&v=\tau(e),\\
0,&\text{otherwise}.
\end{cases}
$$

Positive source vector entries represent net injection into the vascular graph. Negative entries represent net withdrawal.

### 8.3 Flow Conservation

At a node without storage,

$$
\sum_{e\in\delta^+(v)}Q_e-
\sum_{e\in\delta^-(v)}Q_e
=s_v.
$$

In matrix form,

$$
\boxed{
B\mathbf q=\mathbf s
}
$$

where:

$$
\delta^+(v)=\text{outgoing edges},
\qquad
\delta^-(v)=\text{incoming edges}.
$$

**HYD-004.** Every internal, non-storage node MUST satisfy (s_v=0).

**HYD-005.** For each connected component without accumulation,

$$
\sum_{v}s_v=0.
$$

### 8.4 Pressure–Flow Relation

Let

$$
\mathbf p=
\begin{bmatrix}
p_1&\cdots&p_{|V|}
\end{bmatrix}^{\mathsf T}
$$

be the nodal-pressure vector.

The reference pressure drop across each edge is

$$
\Delta\mathbf p=B^{\mathsf T}\mathbf p.
$$

Define edge conductance

$$
g_e=\frac{1}{R_e}
$$

and

$$
K=\operatorname{diag}(g_1,\ldots,g_{|E|}).
$$

The edge-flow vector is

$$
\boxed{
\mathbf q=
KB^{\mathsf T}\mathbf p
}
$$

and the nodal system is

$$
\boxed{
BK B^{\mathsf T}\mathbf p=\mathbf s.
}
$$

The matrix

$$
L_H=BK B^{\mathsf T}
$$

is the hydraulic weighted graph Laplacian.

### 8.5 Boundary Conditions and Solvability

**HYD-006.** Every connected simulated component MUST have sufficient boundary conditions to determine pressure and flow.

For prescribed-pressure nodes (D) and unknown-pressure nodes (F),

$$
L_{FF}\mathbf p_F
=
\mathbf s_F-L_{FD}\mathbf p_D.
$$

**HYD-007.** A component with only prescribed flows MUST satisfy net-flow compatibility and MUST define at least one reference pressure.

**HYD-008.** The solver MUST report singular, inconsistent, or underdetermined boundary-condition configurations.

**HYD-009.** Negative computed edge flow SHALL indicate flow opposite to the edge’s reference orientation and SHALL NOT automatically be treated as an error.

---

## 9. Branching and Scaling Requirements

### 9.1 Generalised Murray Relation

At a branching junction with parent radius (r_p) and daughter radii (r_{d_i}), the generalised branching relation is

$$
\boxed{
r_p^{\gamma_M}
=
\sum_{i=1}^{n}r_{d_i}^{\gamma_M}
}
$$

where $\gamma_M$ is the declared branching exponent.

The classical idealised value is

$$
\gamma_M=3.
$$

**BRN-001.** Murray scaling SHALL be treated as a selectable modelling constraint or validation metric, not as a universal anatomical identity.

**BRN-002.** The selected exponent and its anatomical scope MUST be recorded.

A normalised Murray residual MAY be computed as

$$
\varepsilon_M=
\frac{
\left|
r_p^{\gamma_M}
-
\sum_i r_{d_i}^{\gamma_M}
\right|
}{
\max\left(r_p^{\gamma_M},r_{\mathrm{ref}}^{\gamma_M}\right)
}.
$$

### 9.2 Symmetric Procedural Branching

For (b) equal daughter branches,

$$
r_d=b^{-1/\gamma_M}r_p.
$$

After (k) generations,

$$
r_k=r_0b^{-k/\gamma_M}.
$$

If

$$
L_k=L_0\lambda^k
$$

and

$$
N_k=b^k,
$$

then total generation length is

$$
\mathcal L_k=L_0(b\lambda)^k
$$

and total generation volume is

$$
\boxed{
V_k=
\pi r_0^2L_0
\left(
b^{,1-2/\gamma_M}\lambda
\right)^k.
}
$$

For binary classical Murray branching,

$$
b=2,
\qquad
\gamma_M=3,
$$

giving

$$
r_k=r_0,2^{-k/3}
$$

and

$$
V_k=
\pi r_0^2L_0
\left(
2^{1/3}\lambda
\right)^k.
$$

**BRN-003.** A procedural generator MUST evaluate whether total length, area, and material volume remain physiologically and numerically bounded over the generated hierarchy.

---

## 10. Junction Geometry and Optimisation

Let

$$
\mathbf d_0,\mathbf d_1,\ldots,\mathbf d_n
$$

be unit direction vectors at a junction.

The angle between branches (i) and (j) is

$$
\theta_{ij}
=
\cos^{-1}
\left(
\operatorname{clamp}
\left(
\mathbf d_i\cdot\mathbf d_j,-1,1
\right)
\right).
$$

A permitted junction objective is

$$
J=
w_V
\sum_{e\in E_J}
\pi r_e^2L_e
+
w_D
\sum_{e\in E_J}
R_eQ_e^2,
$$

where (E_J) is the set of edges incident to the junction.

An optimised junction position $\mathbf x_J$ satisfies

$$
\nabla_{\mathbf x_J}J=0
$$

subject to anatomical and geometric constraints.

For a weighted-length approximation, the equilibrium condition MAY be written

$$
\sum_{i=0}^{n}w_i\mathbf d_i=0.
$$

**JNC-001.** The direction-vector convention and weight definition MUST be declared.

**JNC-002.** Local junction optimisation MUST NOT override organ boundaries, forbidden regions, vessel-clearance constraints, or known anatomical topology.

**JNC-003.** Branching-angle rules SHOULD be validated against the relevant organ, vessel scale, and acquisition modality.

---

## 11. Pulsatile One-Dimensional Flow Model

### 11.1 Governing Variables

For each compliant vessel edge:

$$
A_e(x,t)=\text{cross-sectional area},
$$

$$
Q_e(x,t)=\text{volumetric flow},
$$

$$
P_e(x,t)=\text{internal pressure},
$$

with

$$
x\in[0,L_e].
$$

### 11.2 Conservation of Mass

A vessel without distributed leakage SHALL satisfy

$$
\boxed{
\frac{\partial A_e}{\partial t}
+
\frac{\partial Q_e}{\partial x}
=0.
}
$$

### 11.3 Conservation of Momentum

The one-dimensional momentum equation SHALL have the form

$$
\boxed{
\frac{\partial Q_e}{\partial t}
+
\frac{\partial}{\partial x}
\left(
\alpha_e\frac{Q_e^2}{A_e}
\right)
+
\frac{A_e}{\rho_e}
\frac{\partial P_e}{\partial x}
=
-\frac{f_e}{\rho_e},
}
$$

where:

$$
\alpha_e=\text{momentum correction factor}
$$

and

$$
f_e=\text{axial friction force per unit vessel length}.
$$

**PUL-001.** The friction law (f_e) MUST be defined.

**PUL-002.** The momentum correction factor MUST be prescribed or derived.

**PUL-003.** Area, flow, pressure, and material parameters MUST remain finite over the simulation domain.

### 11.4 Wall Constitutive Law

A permitted elastic wall law is

$$
\boxed{
P_e-P_{\mathrm{ext},e}
=
\frac{\beta_e}{A_{0,e}}
\left(
\sqrt{A_e}-\sqrt{A_{0,e}}
\right).
}
$$

Here:

$$
A_{0,e}=\text{reference area}
$$

and

$$
\beta_e=\text{effective wall-stiffness parameter}.
$$

**PUL-004.** A PH-1 implementation MUST define either $\beta_e$ directly or the material model from which it is derived.

**PUL-005.** The external pressure $P_{\mathrm{ext},e}$ MUST be declared.

**PUL-006.** Viscoelastic, nonlinear, or active-wall laws MAY replace the elastic law, provided the complete constitutive relation and parameters are recorded.

### 11.5 Junction Coupling

At a non-storage junction,

$$
\sum_{e\in E_{\mathrm{in}}(v)}Q_e(v,t)
=
\sum_{e\in E_{\mathrm{out}}(v)}Q_e(v,t).
$$

For a lossless junction, total pressure MAY be coupled using

$$
\Pi_e=
P_e+
\frac{1}{2}\rho_e
\left(
\frac{Q_e}{A_e}
\right)^2.
$$

A lossy junction MAY apply a declared loss relation

$$
\Delta P_{\mathrm{loss}}
=
K_L\frac{\rho u^2}{2}.
$$

**PUL-007.** The junction coupling method MUST enforce flow conservation.

**PUL-008.** The treatment of total-pressure continuity, energy loss, and characteristic compatibility MUST be declared.

### 11.6 Initial and Boundary Conditions

**PUL-009.** A PH-1 simulation MUST define initial fields for (A_e), (Q_e), and (P_e), or provide a deterministic initialisation procedure.

**PUL-010.** Inlet and outlet conditions MUST specify pressure, flow, impedance, characteristic, pump, valve, or coupled lumped-element behaviour.

**PUL-011.** The implementation MUST report boundary incompatibilities and failure to achieve numerical convergence.

---

## 12. Wave Propagation and Impedance

For a general pressure-area relation, local wave speed SHALL be defined by

$$
\boxed{
c_e(A)
=
\sqrt{
\frac{A}{\rho_e}
\frac{\partial P_e}{\partial A}
}.
}
$$

For the wall law in Section 11.4, the reference-state wave speed is

$$
c_{0,e}
=
\sqrt{
\frac{\beta_e}
{2\rho_e\sqrt{A_{0,e}}}
}.
$$

A thin-wall Moens–Korteweg approximation MAY be used:

$$
\boxed{
c_e
\approx
\sqrt{
\frac{Y_eh_e}
{2\rho_er_e}
}.
}
$$

The characteristic impedance is

$$
\boxed{
Z_{c,e}
=
\frac{\rho_ec_e}{A_e}.
}
$$

For downstream daughter branches acting in parallel,

$$
\frac{1}{Z_{\mathrm{down}}}
=
\sum_i
\frac{1}{Z_{d_i}}.
$$

A linearised reflection coefficient is

$$
\boxed{
\Gamma
=
\frac{
Z_{\mathrm{down}}-Z_{\mathrm{up}}
}{
Z_{\mathrm{down}}+Z_{\mathrm{up}}
}.
}
$$

**WAV-001.** The wave-speed model MUST be consistent with the selected wall constitutive law.

**WAV-002.** Impedance and reflection calculations MUST state whether they are local, frequency-independent approximations or frequency-dependent models.

**WAV-003.** A PH-1 validation report SHOULD include wave-transmission or reflection metrics when large arteries are represented.

---

## 13. Multiscale Representation

### 13.1 Hierarchical Network

A multiscale model SHALL support the decomposition

$$
G=
G_{\mathrm{large}}
\cup
G_{\mathrm{medium}}
\cup
G_{\mathrm{micro}}
\cup
G_{\mathrm{effective}}.
$$

The components represent:

| Component                | Representation                                                      |
| ------------------------ | ------------------------------------------------------------------- |
| $G_{\mathrm{large}}$     | Explicit major arteries and veins                                   |
| $G_{\mathrm{medium}}$    | Explicit image-resolved or named branches                           |
| $G_{\mathrm{micro}}$     | Procedurally generated arterioles, venules, or selected capillaries |
| $G_{\mathrm{effective}}$ | Lumped, porous, homogenised, or impedance-based vascular beds       |

**MSL-001.** The resolution boundary between explicit and effective vessels MUST be declared.

**MSL-002.** Every transition between representation scales MUST conserve mean flow.

**MSL-003.** Pressure or impedance continuity MUST be enforced according to the selected coupling model.

### 13.2 Windkessel Terminal Model

A three-element Windkessel terminal MAY be represented by

$$
P_{\mathrm{in}}-P_C=R_1Q
$$

and

$$
\boxed{
C\frac{dP_C}{dt}
=
Q-
\frac{P_C-P_{\mathrm{out}}}{R_2}.
}
$$

Here:

$$
R_1=\text{proximal resistance},
$$

$$
C=\text{terminal compliance},
$$

$$
R_2=\text{distal resistance}.
$$

**MSL-004.** All terminal parameters MUST be positive unless a non-passive component is explicitly intended.

**MSL-005.** The downstream reference pressure $P_{\mathrm{out}}$ MUST be defined.

**MSL-006.** Terminal impedances SHOULD be calibrated to match available pressure, flow, organ-resistance, or waveform data.

### 13.3 Tissue Supply and Drainage

Let

$$
\mathcal T={T_1,\ldots,T_N}
$$

be the set of tissue regions.

Each tissue region SHALL define:

$$
d_i^{\mathrm{in}}=\text{required arterial delivery},
$$

$$
d_i^{\mathrm{out}}=\text{required venous drainage},
$$

and, when modelled,

$$
m_i=\text{exchange or metabolic demand}.
$$

**MSL-007.** Each represented tissue region MUST identify its supplying arterial terminal set and draining venous terminal set, or declare why one side is outside the model scope.

**MSL-008.** Tissue coupling MUST preserve volumetric balance unless filtration, storage, haemorrhage, or another source term is explicitly modelled.

**MSL-009.** Effective tissue beds MUST declare their resistance, compliance, permeability, exchange, or constitutive parameters.

---

## 14. Image-Derived Reconstruction

### 14.1 Processing Pipeline

An IR-1 implementation SHALL implement or import the following logical pipeline:

$$
I(\mathbf x)
\rightarrow
S(\mathbf x)
\rightarrow
K(\mathbf x)
\rightarrow
G_{\mathrm{raw}}
\rightarrow
\widehat G
\rightarrow
\mathcal X.
$$

Here:

$$
I(\mathbf x)=\text{input image},
$$

$$
S(\mathbf x)=\text{vessel segmentation},
$$

$$
K(\mathbf x)=\text{centreline or skeleton},
$$

$$
G_{\mathrm{raw}}=\text{initial graph},
$$

$$
\widehat G=\text{repaired and validated graph}.
$$

### 14.2 Segmentation

A binary segmentation MAY be defined as

$$
S(\mathbf x)
=
\mathbf 1
\left[
P(\text{vessel}\mid I,\mathbf x)\geq\tau_S
\right].
$$

**REC-001.** The segmentation method, threshold, model version, and preprocessing operations MUST be recorded.

**REC-002.** The segmentation MUST retain the image-to-world coordinate transform.

**REC-003.** A probabilistic segmentation SHOULD retain vessel-confidence values rather than discarding them after thresholding.

### 14.3 Centreline Extraction

The skeleton or centreline set SHALL be derived as

$$
K=\operatorname{Skeleton}(S)
$$

or by an equivalent centreline-estimation method.

For a skeleton point (x), define neighbourhood degree

$$
d(x)=
\left|
\mathcal N(x)\cap K
\right|.
$$

A basic classification is

$$
\begin{cases}
d(x)=1, & \text{terminal},\\
d(x)=2, & \text{interior centreline point},\\
d(x)\geq3, & \text{junction candidate}.
\end{cases}
$$

**REC-004.** The voxel-neighbourhood convention, including 6-, 18-, or 26-connectivity, MUST be declared.

**REC-005.** Clusters of adjacent junction voxels MUST be consolidated into stable graph nodes.

**REC-006.** Degree-two centreline chains SHOULD be compressed into single graph edges.

### 14.4 Radius Estimation

A distance-transform estimate MAY use

$$
r(x)
\approx
\min_{\mathbf y\in\partial S}
\left|
\mathbf x-\mathbf y
\right|.
$$

Alternative estimates MAY use local cross-sectional fitting, level sets, lumen contours, or model-based reconstruction.

**REC-007.** The radius-estimation method MUST be recorded for every edge or edge group.

**REC-008.** Radius uncertainty SHOULD account for image resolution, partial-volume effects, segmentation uncertainty, and centreline offset.

### 14.5 Geometric Smoothing

A centreline MAY be smoothed by solving

$$
\widehat{\mathbf c}_e
=
\arg\min_{\mathbf c}
\left[
\sum_k
w_k
\left|
\mathbf c(s_k)-\mathbf x_k
\right|^2
+
\lambda_S
\int
\left|
\mathbf c''(s)
\right|^2ds
\right].
$$

**REC-009.** Smoothing MUST preserve edge endpoints within the endpoint tolerance.

**REC-010.** Smoothing MUST NOT introduce vessel crossings, departures from the segmented lumen, or topology changes unless explicitly authorised by the repair stage.

### 14.6 Topology Repair

A repaired graph MAY be obtained by

$$
\boxed{
\widehat G
=
\arg\min_G
\left[
\lambda_I E_{\mathrm{image}}
+
\lambda_G E_{\mathrm{geometry}}
+
\lambda_T E_{\mathrm{topology}}
+
\lambda_H E_{\mathrm{haemodynamics}}
\right].
}
$$

The terms SHALL represent:

$$
\begin{aligned}
E_{\mathrm{image}} &= \text{disagreement with image evidence},\\
E_{\mathrm{geometry}} &= \text{curvature, radius, or surface inconsistency},\\
E_{\mathrm{topology}} &= \text{connectivity or anatomical inconsistency},\\
E_{\mathrm{haemodynamics}} &= \text{pressure, flow, or resistance implausibility}.
\end{aligned}
$$

**REC-011.** Every automatic topology repair MUST retain an auditable record of added, removed, merged, or reconnected graph elements.

**REC-012.** An implementation MUST distinguish image-observed vessels from algorithmically inferred vessels.

---

## 15. Synthetic Vascular Completion

### 15.1 Tissue Demand

Let

$$
Y=
{\mathbf y_1,\ldots,\mathbf y_N}
$$

be tissue demand points within an organ domain $\Omega$, with demand values

$$
d_i>0.
$$

A generated terminal network SHALL deliver the prescribed demand subject to declared tolerances.

### 15.2 Optimisation Objective

A permitted constructive optimisation objective is

$$
\boxed{
\min_{G,\mathbf r,\mathbf x,\mathbf q}
\left[
w_V
\sum_e
\pi r_e^2L_e
+
w_D
\sum_e
R_eQ_e^2
+
w_C
\sum_i
d_i
\operatorname{dist}(\mathbf y_i,G)^2
+
w_AE_{\mathrm{anatomy}}
\right].
}
$$

The terms represent:

$$
\begin{aligned}
\sum_e\pi r_e^2L_e
&=\text{vascular material or volume cost},\\
\sum_eR_eQ_e^2
&=\text{hydraulic dissipation},\\
\sum_i d_i\operatorname{dist}(\mathbf y_i,G)^2
&=\text{unserved-tissue penalty},\\
E_{\mathrm{anatomy}}
&=\text{organ, collision, and anatomical penalties}.
\end{aligned}
$$

### 15.3 Constraints

A generated network SHALL satisfy

$$
B\mathbf q=\mathbf s,
$$

$$
r_e\geq r_{\min}>0,
$$

$$
\mathbf c_e(\ell)\in\Omega,
$$

and, when Murray scaling is selected,

$$
r_p^{\gamma_M}
=
\sum_d r_d^{\gamma_M}
$$

within the declared tolerance.

The generator SHALL also enforce declared constraints on:

$$
\begin{aligned}
&\text{organ boundaries},\\
&\text{forbidden anatomical regions},\\
&\text{vessel separation and collision},\\
&\text{minimum and maximum radius},\\
&\text{minimum segment length},\\
&\text{maximum curvature},\\
&\text{branching degree},\\
&\text{pressure and flow limits},\\
&\text{terminal tissue coverage}.
\end{aligned}
$$

**SYN-001.** Generated vessels MUST be labelled as synthetic.

**SYN-002.** The random seed or deterministic generation state MUST be stored when stochastic generation is used.

**SYN-003.** A generated tree MAY enforce $Q_e\geq0$ relative to its construction orientation. A graph containing collateral loops or reversible flow SHALL permit signed flows.

**SYN-004.** The generator MUST report demand points or tissue regions that cannot be served without violating constraints.

---

## 16. Graph-Theoretic Validation Metrics

### 16.1 Node Degree

For adjacency matrix (A_G),

$$
k_i=\sum_j(A_G)_{ij}.
$$

Directed in-degree and out-degree SHOULD be reported separately.

### 16.2 Branch Ordering

For a tree or declared acyclic subtree, Strahler order SHALL be defined by

$$
\omega_p=
\begin{cases}
\omega+1,
&\omega_1=\omega_2=\omega\\[2mm]
\max(\omega_1,\omega_2),
&\omega_1\neq\omega_2.
\end{cases}
$$

**MET-001.** Strahler ordering MUST NOT be applied directly to a cyclic graph without first declaring the extracted tree or cycle-handling rule.

### 16.3 Weighted Shortest Path

For nodes (u,v),

$$
d_w(u,v)
=
\min_{\pi:u\leadsto v}
\sum_{e\in\pi}w_e.
$$

Permitted weights include

$$
w_e=L_e,
$$

$$
w_e=R_e,
$$

and

$$
w_e=\frac{L_e}{u_e},
$$

representing geometric distance, hydraulic resistance, and transit time.

### 16.4 Cycle Rank

For the underlying undirected graph,

$$
\boxed{
\beta_1=
|E|-|V|+c,
}
$$

where (c) is the number of connected components.

The cycle rank SHALL be used to quantify independent collateral or anastomotic loops.

### 16.5 Betweenness Centrality

Node betweenness centrality is

$$
C_B(v)
=
\sum_{s\neq v\neq t}
\frac{
\sigma_{st}(v)
}{
\sigma_{st}
},
$$

where $\sigma_{st}$ is the number of selected shortest paths from (s) to (t), and (\sigma_{st}(v)) is the number passing through (v).

**MET-002.** The implementation MUST state whether centrality is calculated on the directed, undirected, weighted, or unweighted graph.

**MET-003.** Graph metrics SHALL be treated as validation and comparison measures unless an anatomy-specific acceptance range is separately defined.

---

## 17. Validation and Acceptance Criteria

### 17.1 Declared Tolerances

A conforming implementation SHALL declare at least:

$$
\varepsilon_{\mathrm{endpoint}},
\quad
\varepsilon_{\mathrm{radius}},
\quad
\varepsilon_{\mathrm{mass}},
\quad
\varepsilon_{\mathrm{constitutive}},
\quad
\varepsilon_{\mathrm{solver}},
\quad
\varepsilon_{\mathrm{demand}}.
$$

Tolerances SHALL include units or be explicitly normalised.

### 17.2 Topology Validation

**VAL-001.** All node and edge identifiers MUST be unique.

**VAL-002.** Every edge endpoint MUST reference an existing node.

**VAL-003.** Every simulated connected component MUST contain valid boundary or coupling conditions.

**VAL-004.** Unexpected disconnected components, isolated nodes, self-loops, and duplicate edges MUST be reported.

**VAL-005.** Each tissue region within scope MUST be reachable from its declared arterial source and connected to its declared venous drainage path or effective outlet.

### 17.3 Geometry Validation

The edge endpoint error SHALL be evaluated as

$$
\varepsilon_{\mathrm{end},e}
=
\max
\left(
\left|
\mathbf c_e(0)-\mathbf x_{\sigma(e)}
\right|,
\left|
\mathbf c_e(L_e)-\mathbf x_{\tau(e)}
\right|
\right).
$$

**VAL-006.** A geometric edge is acceptable only when

$$
\varepsilon_{\mathrm{end},e}
\leq
\varepsilon_{\mathrm{endpoint}}.
$$

**VAL-007.** All radii and areas MUST remain positive.

**VAL-008.** Curvature, tortuosity, and radius discontinuities exceeding declared limits MUST be reported.

**VAL-009.** Image-derived centrelines SHOULD remain inside the segmented lumen within the declared image-agreement tolerance.

**VAL-010.** Surface and volume meshes MUST pass the mesh checks required by Section 7.5.

### 17.4 Steady Haemodynamic Validation

The normalised mass-conservation residual SHALL be computed as

$$
\varepsilon_{\mathrm{mass}}
=
\frac{
\left|
B\mathbf q-\mathbf s
\right|_2
}{
\max
\left(
|\mathbf s|_2,
q_{\mathrm{ref}}
\right)
}.
$$

The pressure–flow constitutive residual SHALL be computed as

$$
\varepsilon_{\mathrm{edge}}
=
\frac{
\left|
\mathbf q-
KB^{\mathsf T}\mathbf p
\right|_2
}{
\max
\left(
|\mathbf q|_2,
q_{\mathrm{ref}}
\right)
}.
$$

**VAL-011.** SH-1 conformance requires

$$
\varepsilon_{\mathrm{mass}}
\leq
\varepsilon_{\mathrm{mass}}^{\mathrm{declared}}
$$

and

$$
\varepsilon_{\mathrm{edge}}
\leq
\varepsilon_{\mathrm{constitutive}}^{\mathrm{declared}}.
$$

**VAL-012.** The solver MUST report pressure, flow, and resistance values that are non-finite or outside declared physiological or numerical bounds.

### 17.5 Pulsatile Validation

PH-1 validation SHALL evaluate:

$$
\frac{\partial A}{\partial t}
+
\frac{\partial Q}{\partial x},
$$

the momentum-equation residual, junction mass balance, wall-law consistency, and terminal-condition residuals.

**VAL-013.** The numerical method MUST report its spatial and temporal discretisation.

**VAL-014.** The implementation MUST report stability violations, non-physical negative area, failed characteristic coupling, and unresolved waveform divergence.

**VAL-015.** Cycle-to-cycle periodicity SHOULD be checked when a periodic cardiac input is used.

### 17.6 Tissue-Coverage Validation

For delivered tissue flow $\widehat{\mathbf d}$ and target demand $\mathbf d$,

$$
\varepsilon_{\mathrm{demand}}
=
\frac{
\left|
\widehat{\mathbf d}-\mathbf d
\right|_1
}{
\max
\left(
|\mathbf d|_1,
d_{\mathrm{ref}}
\right)
}.
$$

**VAL-016.** MS-1 and SG-1 conformance require

$$
\varepsilon_{\mathrm{demand}}
\leq
\varepsilon_{\mathrm{demand}}^{\mathrm{declared}}.
$$

**VAL-017.** Unserved, oversupplied, or disconnected tissue regions MUST be identified individually.

### 17.7 Provenance and Uncertainty

**VAL-018.** Every vessel and parameter MUST be distinguishable as measured, segmented, fitted, inferred, generated, calibrated, or assumed.

**VAL-019.** Uncertainty SHOULD be propagated into derived resistance, pressure, flow, and tissue-delivery results.

**VAL-020.** Manual edits MUST be auditable.

---

## 18. Interchange Structure

A conforming interchange package SHALL contain the following top-level objects:

| Object                | Contents                                                                                                 |
| --------------------- | -------------------------------------------------------------------------------------------------------- |
| `metadata`            | Specification version, conformance profiles, subject or model identifier, coordinate system, unit system |
| `nodes`               | Node records                                                                                             |
| `edges`               | Edge records                                                                                             |
| `tissue_regions`      | Tissue geometry, demand, supply, drainage, and exchange properties                                       |
| `boundary_conditions` | Pressure, flow, impedance, pump, valve, or mixed conditions                                              |
| `initial_conditions`  | REQUIRED for PH-1                                                                                        |
| `states`              | Steady or time-dependent haemodynamic results                                                            |
| `provenance`          | Source images, algorithms, calibration data, manual modifications                                        |
| `uncertainty`         | Parameter and geometry uncertainty                                                                       |
| `validation`          | Tolerances, residuals, failures, warnings, and acceptance status                                         |
| `mesh`                | OPTIONAL surface or volume mesh with graph mapping                                                       |

**DAT-001.** The package MUST identify the specification version.

**DAT-002.** Every stored quantity MUST be associated with a unit directly or through a declared schema-wide unit convention.

**DAT-003.** Referenced arrays, meshes, images, and time-series files MUST use stable identifiers and integrity checks.

**DAT-004.** Missing values MUST be represented explicitly and MUST NOT be silently replaced with zero.

**DAT-005.** Derived values SHOULD identify the formula, algorithm, or software version used to calculate them.

---

## 19. Compact Computational Formulation

A conforming steady-state implementation SHALL be reducible to

$$
\boxed{
\begin{aligned}
G&=(V,E,\sigma,\tau)\\[1mm]
R_e
&=
\int_0^{L_e}
\frac{8\mu_e(\ell)}
{\pi r_e(\ell)^4}
\,d\ell\\[1mm]
B\mathbf q&=\mathbf s\\[1mm]
\mathbf q&=KB^{\mathsf T}\mathbf p\\[1mm]
BK B^{\mathsf T}\mathbf p&=\mathbf s\\[1mm]
r_p^{\gamma_M}
&\approx
\sum_d r_d^{\gamma_M}\\[1mm]
\widehat G
&=
\arg\min_G
\left(
E_{\mathrm{geometry}}
+
E_{\mathrm{flow}}
+
E_{\mathrm{coverage}}
+
E_{\mathrm{anatomy}}
\right).
\end{aligned}
}
$$

A conforming pulsatile implementation SHALL replace or augment the algebraic edge relation with

$$
\frac{\partial A_e}{\partial t}
+
\frac{\partial Q_e}{\partial x}
=0
$$

and

$$
\frac{\partial Q_e}{\partial t}
+
\frac{\partial}{\partial x}
\left(
\alpha_e\frac{Q_e^2}{A_e}
\right)
+
\frac{A_e}{\rho_e}
\frac{\partial P_e}{\partial x}
=
-\frac{f_e}{\rho_e}.
$$

The complete modelling progression is

$$
\boxed{
\text{anatomy}
\rightarrow
\text{geometry}
\rightarrow
\text{graph}
\rightarrow
\text{hydraulic network}
\rightarrow
\text{wave network}
\rightarrow
\text{multiscale vascular replica}.
}
$$

A conforming replica SHALL address five independent forms of correctness:

$$
\boxed{
\text{topology},
\quad
\text{geometry},
\quad
\text{haemodynamics},
\quad
\text{tissue coverage},
\quad
\text{multiscale consistency}.
}
$$

---

## Appendix A — Informative Numerical Examples

### A.1 Radius Sensitivity

Because

$$
R\propto r^{-4},
$$

a reduction from (r) to (0.9r) gives

$$
\frac{R_{\mathrm{new}}}{R_{\mathrm{old}}}
=
\left(
\frac{1}{0.9}
\right)^4
\approx1.524.
$$

Under the idealised Poiseuille model, a ten-percent radius reduction therefore produces approximately a (52.4%) resistance increase.

### A.2 Murray-Compatible Bifurcation

For

$$
r_p=4\ \mathrm{mm},
\qquad
r_1=3\ \mathrm{mm},
\qquad
\gamma_M=3,
$$

the second daughter radius is

$$
r_2
= \sqrt[3]{r_p^3-r_1^3}
= \sqrt[3]{4^3-3^3}
= \sqrt[3]{37}
\approx 3.33\ \mathrm{mm}.
$$

Thus,

$$
4^3
\approx
3^3+3.33^3.
$$

---

## Appendix B — Informative References

All seven entries were resolved and confirmed to be real publications on 2026-07-10
(Nature/npj records verified through Crossref DOI metadata). Titles were corrected to
match the published versions where they differed from earlier drafts; the reader is
still expected to verify that each source supports the specific claim it is attached to.

1. Epifanov, R., Fedotova, Y., Dyachuk, S., Gostev, A., Karpenko, A., Mullyadzhanov, R.
   *The Robust Vessel Segmentation and Centerline Extraction: One-Stage Deep Learning Approach.*
   **Journal of Imaging** 11(7):209, 2025. DOI: 10.3390/jimaging11070209.
   [https://pmc.ncbi.nlm.nih.gov/articles/PMC12295992/](https://pmc.ncbi.nlm.nih.gov/articles/PMC12295992/)

2. Hagmeijer, R., Venner, C. H.
   *Critical review of Murray’s theory for optimal branching in fluidic networks.*
   **arXiv:1812.09706**, 2018.
   [https://ar5iv.labs.arxiv.org/html/1812.09706](https://ar5iv.labs.arxiv.org/html/1812.09706)

3. Altieri Correa, S., Kachabi, A., Colebank, M. J., Miles, C. E., Chesler, N. C.
   *Revisiting Murray’s Law in Pulmonary Arteries: Exploring branching patterns and principles.*
   **Journal of Biomechanical Engineering**, 2025.
   [https://pmc.ncbi.nlm.nih.gov/articles/PMC12834150/](https://pmc.ncbi.nlm.nih.gov/articles/PMC12834150/)

4. Tekin, E., Hunt, D., Newberry, M. G., Savage, V. M.
   *Do Vascular Networks Branch Optimally or Randomly across Spatial Scales?*
   **PLoS Computational Biology** 12(11):e1005223, 2016. DOI: 10.1371/journal.pcbi.1005223.
   [https://pmc.ncbi.nlm.nih.gov/articles/PMC5130167/](https://pmc.ncbi.nlm.nih.gov/articles/PMC5130167/)

5. *Mapping the arterial vascular network in an intact human kidney using hierarchical phase-contrast tomography.*
   **npj Imaging**, 2025. DOI: 10.1038/s44303-025-00090-2.
   [https://www.nature.com/articles/s44303-025-00090-2](https://www.nature.com/articles/s44303-025-00090-2)

6. *Sparse and transferable three-dimensional dynamic vascular reconstruction for instantaneous diagnosis.*
   **Nature Machine Intelligence**, 2025. DOI: 10.1038/s42256-025-01025-7.
   [https://www.nature.com/articles/s42256-025-01025-7](https://www.nature.com/articles/s42256-025-01025-7)

7. *A hybrid approach to full-scale reconstruction of renal arterial network.*
   **Scientific Reports**, 2023. DOI: 10.1038/s41598-023-34739-y.
   [https://www.nature.com/articles/s41598-023-34739-y](https://www.nature.com/articles/s41598-023-34739-y)

---

## Appendix C — Source Basis

**Provenance and epistemic status of this document** (per PALS's Law and `CLAUDE.md` Rule 2):

- **Base specification (§1–§19).** The vascular / haemodynamic model carries the
  identifier `VGR-HR-001`. Its original authorship is not recorded in this repository
  and has **not** been independently verified here. The physical, physiological, and
  numerical claims (constitutive laws, wall models, wave-speed relations, branching
  exponents) are standard forms in the haemodynamics literature but require
  **domain-expert verification** before any clinical, diagnostic, or engineering use.
  This document establishes computational conformance only (§1) — not physical or
  clinical validity.
- **Mathematics.** The equations were normalised from a corrupted LaTeX→Markdown
  conversion into renderable KaTeX on 2026-07-10. The repair was structural
  (delimiters, row breaks, subscripts, stray heading artifacts). A spot-check confirmed
  the Poiseuille resistance, Murray relation, Moens–Korteweg and reference-state wave
  speeds, the cycle rank $\beta_1 = |E| - |V| + c$, and both Appendix A numerical
  examples. A full independent re-derivation of every relation has **not** been performed.
- **Codebase analogy (§0).** Authored for this repository as an interpretive lens. It is
  **not** a proven isomorphism; the load-bearing / metaphorical split in §0.1–§0.2 is the
  boundary of what may be relied upon.
- **References (Appendix B).** All seven URLs were resolved and confirmed real on
  2026-07-10 (Nature/npj via Crossref). Several titles were corrected to the published
  wording. Claim-level support is still the reader's responsibility to verify.
- **This layer's authorship.** Codebase-analogy section, math normalisation, reference
  verification, and this Source Basis: Claude Fable 5 via Claude Code, 2026-07-10.