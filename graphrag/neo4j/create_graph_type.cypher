// ── BunkyoWelfare Schema (Neo4j 2026.02+)
//
// GRAPH TYPE is a preview feature not yet available in Neo4j 2026.02.3.
// As a substitute, we use:
//   - Property existence constraints (node labels must have required props)
//   - Uniqueness constraints (id-based)
//   - Indexes for query performance
//   - APOC triggers for relationship endpoint validation
//
// Reference: ontology.md §GRAPH TYPE スキーマ

// ── 1. Uniqueness constraints (id-based) ──
CREATE CONSTRAINT svc_id_unique IF NOT EXISTS FOR (n:Service)
  REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT cat_id_unique IF NOT EXISTS FOR (n:ServiceCategory)
  REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT tc_id_unique IF NOT EXISTS FOR (n:TargetCategory)
  REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT nb_id_unique IF NOT EXISTS FOR (n:Notebook)
  REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT contact_id_unique IF NOT EXISTS FOR (n:Contact)
  REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT ref_id_unique IF NOT EXISTS FOR (n:Reference)
  REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT facility_id_unique IF NOT EXISTS FOR (n:Facility)
  REQUIRE n.id IS UNIQUE;

// ── 2. Property existence constraints (required properties) ──
CREATE CONSTRAINT svc_name_exists IF NOT EXISTS FOR (n:Service)
  REQUIRE n.name IS NOT NULL;
CREATE CONSTRAINT cat_name_exists IF NOT EXISTS FOR (n:ServiceCategory)
  REQUIRE n.name IS NOT NULL;
CREATE CONSTRAINT tc_code_exists IF NOT EXISTS FOR (n:TargetCategory)
  REQUIRE n.code IS NOT NULL;
CREATE CONSTRAINT nb_name_exists IF NOT EXISTS FOR (n:Notebook)
  REQUIRE n.name IS NOT NULL;
CREATE CONSTRAINT contact_dept_exists IF NOT EXISTS FOR (n:Contact)
  REQUIRE n.dept IS NOT NULL;
CREATE CONSTRAINT ref_name_exists IF NOT EXISTS FOR (n:Reference)
  REQUIRE n.name IS NOT NULL;
CREATE CONSTRAINT facility_name_exists IF NOT EXISTS FOR (n:Facility)
  REQUIRE n.name IS NOT NULL;

// ── 3. Indexes for performance ──
CREATE INDEX svc_name IF NOT EXISTS FOR (n:Service) ON (n.name);
CREATE INDEX nb_name   IF NOT EXISTS FOR (n:Notebook) ON (n.name);
CREATE INDEX dept      IF NOT EXISTS FOR (n:Contact)  ON (n.dept);
CREATE INDEX cat_name  IF NOT EXISTS FOR (n:ServiceCategory) ON (n.name);

// ── 4. GRAPH TYPE reference (Neo4j future version) ──
// The following is the intended GRAPH TYPE schema when the feature becomes stable.
// It is kept here as a machine-readable specification.
/*
CREATE GRAPH TYPE BunkyoWelfare {
  ServiceCategory  ({ name       :: STRING NOT NULL }),
  TargetCategory   ({ code       :: STRING NOT NULL, name :: STRING }),
  Notebook         ({ name       :: STRING NOT NULL, grade_type :: STRING }),
  Contact          ({ dept       :: STRING NOT NULL, phone :: STRING, fax :: STRING, location :: STRING }),
  Reference        ({ name       :: STRING NOT NULL, kind :: STRING, booklet_page :: STRING }),
  Facility         ({ name       :: STRING NOT NULL, phone :: STRING, address :: STRING }),

  Service          ({ name       :: STRING NOT NULL,
                      income_limit :: BOOLEAN,
                      free_hours_per_month :: INTEGER,
                      medical_care_child :: BOOLEAN,
                      age_range   :: STRING,
                      desc        :: STRING,
                      note        :: STRING,
                      excludes    :: LIST<STRING>
                    }),
  Allowance       :: Service,
  MedicalAid      :: Service,
  AssistiveDevice :: Service,
  TransportBenefit :: Service,

  (:Service)-[:HAS_CATEGORY]->(:ServiceCategory),
  (:Service)-[:TARGETS]->(:TargetCategory),
  (:Service)-[:REQUIRES { grades :: LIST<STRING>, grade_note :: STRING }]->(:Notebook),
  (:Service)-[:ADMINISTERED_BY { for :: STRING, hours :: STRING }]->(:Contact),
  (:Service)-[:DEFINED_BY]->(:Reference),
  (:Service)-[:MUTUALLY_EXCLUSIVE_WITH { basis :: STRING }]->(:Service),
  (:Service)-[:RELATED_TO]->(:Service),
  (:Facility)-[:PROVIDED_AT]->(:Service)
};
*/