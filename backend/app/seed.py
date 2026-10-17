"""Realistic demo portfolio (~26 use cases) with deliberate duplicates, overlaps and dependencies.

Loaded on first start when SEED_DEMO_DATA=true and the registry is empty. Analysis runs without LLM
(structural relations + similarity-only candidates) so the demo works with no API key; with a key,
click "Analyze portfolio" to get LLM-judged relations.
"""
from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .auth import hash_password
import secrets

from .models import Decision, Role, Status, UseCase, User
from .services import analysis
from .services.templates import get_default_template
from .services.usecases import create_use_case, validate_risk

log = logging.getLogger(__name__)

USERS = [
    ("marie.dupont@example.com", "Marie Dupont", Role.reviewer.value),
    ("karim.benali@example.com", "Karim Benali", Role.requester.value),
    ("anita.sharma@example.com", "Anita Sharma", Role.requester.value),
    ("lucas.martin@example.com", "Lucas Martin", Role.requester.value),
]

# status, owner index, fields
DEMO = [
    (Status.in_production.value, 1, {
        "title": "Customer document extraction platform (KYC & onboarding)",
        "summary": "A shared document intelligence service that extracts structured data from identity documents, proofs of address and payslips submitted during onboarding, exposing a reusable API to other teams.",
        "business_unit": "Retail Banking Operations",
        "problem_statement": "Back-office teams manually re-key data from 40,000 customer documents a month, causing a 3-day onboarding delay and 6% error rate.",
        "target_users": ["KYC analysts (120)", "Onboarding back-office (60)"],
        "pain_points": ["Manual re-keying", "Rework due to typos", "Onboarding SLA breaches"],
        "ai_capability": "extraction", "human_in_the_loop": "exception_only", "interaction_mode": "api",
        "solution_description": "OCR + LLM extraction pipeline with confidence scoring; low-confidence fields are routed to analysts for review. Exposed as an internal API consumed by onboarding and lending journeys.",
        "data_sources": ["Scanned identity documents", "Proof of address documents", "Payslips"],
        "data_sensitivity": "personal_data", "data_volume": "40k documents / month",
        "source_systems": ["Onboarding DMS", "SharePoint"], "target_systems": ["Core banking (Temenos)", "KYC workflow (Pega)"],
        "value_drivers": ["cost_reduction", "speed_time_to_market", "quality"],
        "kpis": ["Onboarding time 3 days -> same day", "Re-keying error rate 6% -> <1%", "FTE effort -40%"],
        "estimated_annual_value": 1800000, "affected_population": 180, "urgency": "high",
        "data_readiness": 4, "technical_complexity": 3, "change_impact": 2,
        "eu_ai_act_tier": "minimal_risk", "risk_categories": ["privacy", "hallucination_accuracy"],
        "mitigations": ["Confidence thresholds with human review", "PII minimisation and retention policy"],
        "build_approach": "build", "estimated_effort": "L", "teams_involved": ["Data & AI platform", "Onboarding IT"],
        "models_considered": ["Azure Document Intelligence", "GPT-4o"],
    }),
    (Status.approved.value, 2, {
        "title": "Automated triage of incoming supplier invoices",
        "summary": "Classify and route incoming supplier invoices to the right accounts-payable queue and pre-fill SAP entries from the extracted invoice data.",
        "business_unit": "Finance - Procure to Pay",
        "problem_statement": "Accounts payable receives 25,000 invoices a month by email and PDF; agents spend 12 minutes per invoice on classification, coding and matching.",
        "target_users": ["Accounts payable clerks (45)"],
        "pain_points": ["Slow invoice cycle time", "Late payment penalties", "Duplicate invoices paid"],
        "ai_capability": "extraction", "human_in_the_loop": "review_before_action", "interaction_mode": "embedded_in_app",
        "solution_description": "Extract header and line items from invoice PDFs, classify cost center and GL account, detect duplicates and pre-fill SAP; clerk validates before posting.",
        "data_sources": ["Supplier invoices (PDF/email)", "SAP vendor master", "Purchase orders"],
        "data_sensitivity": "confidential", "data_volume": "25k invoices / month",
        "source_systems": ["Shared mailbox (Exchange)", "SharePoint"], "target_systems": ["SAP S/4HANA"],
        "value_drivers": ["cost_reduction", "speed_time_to_market"],
        "kpis": ["Handling time 12 min -> 4 min", "Invoice cycle time -50%", "Duplicate payments -90%"],
        "estimated_annual_value": 900000, "affected_population": 45, "urgency": "high",
        "data_readiness": 4, "technical_complexity": 2, "change_impact": 2,
        "eu_ai_act_tier": "minimal_risk", "dependencies_notes": "Should reuse the document extraction platform built by Retail Operations instead of a new OCR stack.",
        "build_approach": "reuse_existing_platform", "estimated_effort": "M", "teams_involved": ["Finance IT", "Data & AI platform"],
        "models_considered": ["Azure Document Intelligence"],
    }),
    (Status.submitted.value, 3, {
        "title": "Invoice processing assistant for accounts payable",
        "summary": "An AI assistant that reads supplier invoices from the AP mailbox, extracts key fields and proposes the SAP booking so clerks only need to approve.",
        "business_unit": "Finance - Procure to Pay",
        "problem_statement": "Clerks manually read every invoice email and type the data into SAP, which is slow and error-prone.",
        "target_users": ["Accounts payable team"],
        "ai_capability": "extraction", "human_in_the_loop": "review_before_action",
        "solution_description": "LLM reads invoice attachments, extracts supplier, amount, PO number and proposes the booking in SAP for one-click approval.",
        "data_sources": ["Supplier invoices (PDF/email)"], "data_sensitivity": "confidential",
        "source_systems": ["Shared mailbox (Exchange)"], "target_systems": ["SAP S/4HANA"],
        "value_drivers": ["cost_reduction"], "kpis": ["Time per invoice -60%"], "urgency": "medium",
        "estimated_annual_value": 400000, "affected_population": 40, "data_readiness": 3, "technical_complexity": 2,
    }),
    (Status.in_build.value, 1, {
        "title": "IT service desk copilot (ServiceNow)",
        "summary": "A copilot embedded in ServiceNow that suggests ticket categorisation, drafts responses from the knowledge base and proposes resolution steps for L1 agents.",
        "business_unit": "Group IT - Service Management",
        "problem_statement": "L1 agents handle 60,000 tickets a month with a 35% misrouting rate and inconsistent answers; knowledge articles are hard to find.",
        "target_users": ["L1 service desk agents (150)", "L2 support (80)"],
        "pain_points": ["Misrouted tickets", "Long time to first response", "Knowledge not reused"],
        "ai_capability": "search_rag", "human_in_the_loop": "review_before_action", "interaction_mode": "copilot",
        "solution_description": "RAG over the knowledge base and resolved tickets; suggests category, assignment group and a draft reply inside the ServiceNow agent workspace. Agents accept or edit.",
        "data_sources": ["ServiceNow incident tickets", "Knowledge base articles (Confluence)"],
        "data_sensitivity": "internal", "data_volume": "60k tickets / month, 8k KB articles",
        "source_systems": ["ServiceNow", "Confluence"], "target_systems": ["ServiceNow"],
        "value_drivers": ["cost_reduction", "employee_experience", "quality"],
        "kpis": ["Misrouting 35% -> 10%", "Time to first response -40%", "CSAT +10 pts"],
        "estimated_annual_value": 1200000, "affected_population": 230, "urgency": "high",
        "data_readiness": 4, "technical_complexity": 3, "change_impact": 3,
        "eu_ai_act_tier": "minimal_risk", "risk_categories": ["hallucination_accuracy"],
        "build_approach": "build", "estimated_effort": "L", "teams_involved": ["Data & AI platform", "Service management"],
        "models_considered": ["Claude", "internal LangGraph platform"],
    }),
    (Status.qualified.value, 2, {
        "title": "Automatic ticket categorisation and routing",
        "summary": "Classify incoming ServiceNow tickets by category and urgency and auto-assign them to the right resolver group.",
        "business_unit": "Group IT - Service Management",
        "problem_statement": "Tickets are misrouted in a third of cases, adding a day of delay before the right team sees them.",
        "target_users": ["Service desk dispatchers (12)", "Resolver groups"],
        "ai_capability": "classification", "human_in_the_loop": "exception_only", "interaction_mode": "batch",
        "solution_description": "Classifier trained on 2 years of resolved tickets predicts category and assignment group; low-confidence tickets go to dispatchers.",
        "data_sources": ["ServiceNow incident tickets"], "data_sensitivity": "internal",
        "source_systems": ["ServiceNow"], "target_systems": ["ServiceNow"],
        "value_drivers": ["speed_time_to_market", "cost_reduction"], "kpis": ["Misrouting rate -70%", "Mean time to assign -80%"],
        "estimated_annual_value": 500000, "affected_population": 300, "urgency": "medium",
        "data_readiness": 5, "technical_complexity": 2, "change_impact": 2, "eu_ai_act_tier": "minimal_risk",
    }),
    (Status.submitted.value, 3, {
        "title": "HR policy chatbot for employees",
        "summary": "A conversational assistant answering employee questions on HR policies, leave, benefits and payroll calendars, grounded in the HR knowledge base.",
        "business_unit": "Human Resources",
        "problem_statement": "The HR shared service centre answers 15,000 repetitive questions a month; employees wait 2 days for simple answers.",
        "target_users": ["All employees (25,000)", "HR shared service agents (40)"],
        "ai_capability": "conversational_agent", "human_in_the_loop": "none", "interaction_mode": "chat",
        "solution_description": "RAG chatbot over HR policy documents and FAQs in Confluence and SharePoint, with escalation to a human agent and citation of the source policy.",
        "data_sources": ["HR policy documents (SharePoint)", "HR FAQs (Confluence)", "Workday employee data"],
        "data_sensitivity": "personal_data", "source_systems": ["SharePoint", "Confluence", "Workday"],
        "target_systems": ["Microsoft Teams"],
        "value_drivers": ["employee_experience", "cost_reduction"], "kpis": ["Tier-0 resolution rate 60%", "HR ticket volume -40%"],
        "estimated_annual_value": 700000, "affected_population": 25000, "urgency": "medium",
        "data_readiness": 3, "technical_complexity": 2, "change_impact": 2,
        "risk_categories": ["hallucination_accuracy", "privacy"],
    }),
    (Status.draft.value, 3, {
        "title": "CV screening and candidate ranking",
        "summary": "Rank incoming applications against job descriptions to shortlist candidates for recruiters.",
        "business_unit": "Human Resources - Talent Acquisition",
        "problem_statement": "Recruiters receive 200+ applications per role and spend 30% of their time on first screening.",
        "target_users": ["Recruiters (35)"],
        "ai_capability": "recommendation", "human_in_the_loop": "review_before_action",
        "solution_description": "Match CV content to job requirements and produce a ranked shortlist with explanations; recruiters make the final decision.",
        "data_sources": ["Candidate CVs (Workday Recruiting)", "Job descriptions"],
        "data_sensitivity": "personal_data", "source_systems": ["Workday"], "target_systems": ["Workday"],
        "value_drivers": ["cost_reduction", "speed_time_to_market"], "kpis": ["Time to shortlist -50%"],
        "estimated_annual_value": 300000, "affected_population": 35, "urgency": "low",
        "data_readiness": 3, "technical_complexity": 3, "change_impact": 3,
        "risk_categories": ["bias_fairness", "explainability", "privacy"],
    }),
    (Status.qualified.value, 1, {
        "title": "Credit application pre-assessment for SMEs",
        "summary": "Summarise SME credit files and pre-assess eligibility against credit policy to speed up analyst decisions.",
        "business_unit": "Corporate & SME Lending",
        "problem_statement": "Credit analysts spend 4 hours per SME file reading financial statements and checking policy rules before a decision.",
        "target_users": ["Credit analysts (90)"],
        "ai_capability": "summarization", "human_in_the_loop": "review_before_action", "interaction_mode": "copilot",
        "solution_description": "Extract financials from statements, compute policy ratios, summarise the file and flag policy exceptions; the analyst keeps the decision.",
        "data_sources": ["SME financial statements (PDF)", "Credit policy rules", "Core banking account history"],
        "data_sensitivity": "confidential", "source_systems": ["Credit workflow (Pega)", "Core banking (Temenos)"],
        "target_systems": ["Credit workflow (Pega)"],
        "value_drivers": ["speed_time_to_market", "revenue_growth", "risk_reduction"],
        "kpis": ["Time to decision 5 days -> 2 days", "Analyst capacity +40%"],
        "estimated_annual_value": 2500000, "affected_population": 90, "urgency": "high",
        "data_readiness": 3, "technical_complexity": 4, "change_impact": 3,
        "dependencies_notes": "Depends on the document extraction platform for financial statement parsing.",
        "risk_categories": ["bias_fairness", "explainability", "hallucination_accuracy"],
        "eu_ai_act_tier": "high_risk", "eu_ai_act_rationale": "Creditworthiness assessment of natural persons may be involved for sole traders (Annex III, 5(b)).",
        "build_approach": "build", "estimated_effort": "XL", "teams_involved": ["Data & AI platform", "Lending IT", "Model risk management"],
    }),
    (Status.draft.value, 2, {
        "title": "Contract clause extraction and risk flagging",
        "summary": "Extract key clauses from supplier and client contracts and flag deviations from standard terms for legal review.",
        "business_unit": "Legal & Compliance",
        "problem_statement": "Legal reviews 3,000 contracts a year manually; non-standard clauses are missed and renewals are not tracked.",
        "target_users": ["Legal counsels (25)", "Procurement (30)"],
        "ai_capability": "extraction", "human_in_the_loop": "review_before_action",
        "solution_description": "LLM extracts clauses (termination, liability, data protection, SLA) into a structured register and highlights deviations from the clause library.",
        "data_sources": ["Contracts (SharePoint)", "Standard clause library"], "data_sensitivity": "confidential",
        "source_systems": ["SharePoint", "Contract management (Icertis)"], "target_systems": ["Contract management (Icertis)"],
        "value_drivers": ["risk_reduction", "compliance", "cost_reduction"], "kpis": ["Review time per contract -60%", "Missed non-standard clauses -> 0"],
        "estimated_annual_value": 600000, "affected_population": 55, "urgency": "medium",
        "data_readiness": 3, "technical_complexity": 3, "change_impact": 2,
    }),
    (Status.submitted.value, 3, {
        "title": "Call centre conversation summarisation",
        "summary": "Automatically summarise customer calls and generate the after-call note in Salesforce so agents spend less time on wrap-up.",
        "business_unit": "Customer Service",
        "problem_statement": "Agents spend 3 minutes after each of 1.2M yearly calls writing notes that are inconsistent and rarely reused.",
        "target_users": ["Call centre agents (600)"],
        "ai_capability": "summarization", "human_in_the_loop": "review_before_action", "interaction_mode": "embedded_in_app",
        "solution_description": "Speech-to-text then LLM summary with reason for call, actions and sentiment, pre-filled in the Salesforce case for the agent to confirm.",
        "data_sources": ["Call recordings (Genesys)", "Salesforce cases"], "data_sensitivity": "personal_data",
        "source_systems": ["Genesys Cloud", "Salesforce"], "target_systems": ["Salesforce"],
        "value_drivers": ["cost_reduction", "customer_experience", "quality"], "kpis": ["After-call work 180s -> 45s", "Note quality score +30%"],
        "estimated_annual_value": 2100000, "affected_population": 600, "urgency": "high",
        "data_readiness": 3, "technical_complexity": 3, "change_impact": 2,
        "risk_categories": ["privacy", "hallucination_accuracy"],
    }),
    (Status.draft.value, 2, {
        "title": "Real-time agent assist with next best action",
        "summary": "During live calls, surface relevant knowledge, customer context and the next best action to the agent.",
        "business_unit": "Customer Service",
        "problem_statement": "Agents put customers on hold to search for procedures; average handling time is 9 minutes and first-call resolution is 68%.",
        "target_users": ["Call centre agents (600)"],
        "ai_capability": "recommendation", "human_in_the_loop": "review_before_action", "interaction_mode": "copilot",
        "solution_description": "Real-time transcription feeds a RAG assistant that shows procedures and offers; builds on the call summarisation transcription pipeline.",
        "data_sources": ["Call recordings (Genesys)", "Knowledge base articles (Confluence)", "Salesforce customer 360"],
        "data_sensitivity": "personal_data", "source_systems": ["Genesys Cloud", "Salesforce", "Confluence"],
        "target_systems": ["Salesforce"],
        "value_drivers": ["customer_experience", "revenue_growth"], "kpis": ["AHT -15%", "FCR 68% -> 80%"],
        "estimated_annual_value": 1500000, "affected_population": 600, "urgency": "medium",
        "data_readiness": 2, "technical_complexity": 4, "change_impact": 3,
        "dependencies_notes": "Requires the real-time transcription stream from the call summarisation initiative.",
    }),
    (Status.in_production.value, 1, {
        "title": "Transaction fraud anomaly detection",
        "summary": "Detect anomalous card and transfer transactions in real time and route them to fraud analysts.",
        "business_unit": "Risk - Fraud",
        "problem_statement": "Rule-based fraud detection produces 90% false positives, saturating the 50-analyst fraud team and delaying real cases.",
        "target_users": ["Fraud analysts (50)"],
        "ai_capability": "anomaly_detection", "human_in_the_loop": "review_before_action", "interaction_mode": "batch",
        "solution_description": "Gradient boosting + graph features on transaction streams, scoring in <100ms; analysts review flagged transactions.",
        "data_sources": ["Card transactions", "SEPA transfers", "Customer profiles"], "data_sensitivity": "personal_data",
        "source_systems": ["Core banking (Temenos)", "Card processor"], "target_systems": ["Fraud case management (Actimize)"],
        "value_drivers": ["risk_reduction", "cost_reduction"], "kpis": ["False positives -60%", "Fraud losses -25%"],
        "estimated_annual_value": 4000000, "affected_population": 50, "urgency": "critical",
        "data_readiness": 5, "technical_complexity": 4, "change_impact": 2,
        "eu_ai_act_tier": "minimal_risk", "risk_categories": ["bias_fairness", "model_drift"],
        "build_approach": "build", "estimated_effort": "XL", "teams_involved": ["Fraud analytics", "Data & AI platform"],
    }),
    (Status.submitted.value, 2, {
        "title": "AML transaction monitoring alert triage",
        "summary": "Prioritise and pre-document anti-money-laundering alerts so investigators focus on the highest-risk cases.",
        "business_unit": "Compliance - Financial Crime",
        "problem_statement": "Investigators clear 30,000 AML alerts a month, 95% of which are closed as false positives after 40 minutes of manual research each.",
        "target_users": ["AML investigators (70)"],
        "ai_capability": "classification", "human_in_the_loop": "review_before_action",
        "solution_description": "Risk-score alerts using transaction patterns and customer data, auto-draft the investigation narrative from gathered evidence; investigator decides.",
        "data_sources": ["SEPA transfers", "Card transactions", "Customer profiles", "Sanctions lists"],
        "data_sensitivity": "personal_data", "source_systems": ["Core banking (Temenos)", "Fraud case management (Actimize)"],
        "target_systems": ["Fraud case management (Actimize)"],
        "value_drivers": ["compliance", "cost_reduction", "risk_reduction"], "kpis": ["Investigation time -40%", "SAR quality score +20%"],
        "estimated_annual_value": 1800000, "affected_population": 70, "urgency": "high",
        "data_readiness": 4, "technical_complexity": 4, "change_impact": 2,
        "dependencies_notes": "Shares transaction feature pipeline with fraud anomaly detection.",
        "risk_categories": ["bias_fairness", "explainability"],
    }),
    (Status.qualified.value, 3, {
        "title": "Regulatory watch and impact summaries",
        "summary": "Monitor regulatory publications (EBA, ECB, AMF, EU) and produce impact summaries mapped to internal policies.",
        "business_unit": "Legal & Compliance",
        "problem_statement": "The compliance team reads 400 regulatory publications a month and misses relevant updates; impact analysis takes weeks.",
        "target_users": ["Compliance officers (30)"],
        "ai_capability": "summarization", "human_in_the_loop": "review_before_action", "interaction_mode": "batch",
        "solution_description": "Crawl regulator sites, classify relevance, summarise and map each publication to affected internal policies and owners.",
        "data_sources": ["Regulator publications (web)", "Internal policy register (SharePoint)"], "data_sensitivity": "internal",
        "source_systems": ["SharePoint"], "target_systems": ["GRC tool (ServiceNow IRM)"],
        "value_drivers": ["compliance", "risk_reduction"], "kpis": ["Time to impact assessment 3 weeks -> 3 days", "Missed updates -> 0"],
        "estimated_annual_value": 500000, "affected_population": 30, "urgency": "medium",
        "data_readiness": 3, "technical_complexity": 2, "change_impact": 1, "eu_ai_act_tier": "minimal_risk",
    }),
    (Status.draft.value, 3, {
        "title": "Marketing content generation for campaigns",
        "summary": "Generate on-brand drafts of emails, banners and social posts for product campaigns, with compliance pre-checks.",
        "business_unit": "Marketing",
        "problem_statement": "Campaign content takes 3 weeks from brief to approval, with 4 rounds of compliance review.",
        "target_users": ["Marketing managers (20)", "Compliance reviewers (5)"],
        "ai_capability": "generation", "human_in_the_loop": "review_before_action", "interaction_mode": "chat",
        "solution_description": "LLM generates variants from a brief and brand guidelines, runs a compliance checklist and flags claims needing substantiation.",
        "data_sources": ["Brand guidelines", "Past campaign assets (DAM)", "Compliance checklist"], "data_sensitivity": "internal",
        "source_systems": ["Digital asset management (Bynder)"], "target_systems": ["Salesforce Marketing Cloud"],
        "value_drivers": ["speed_time_to_market", "cost_reduction"], "kpis": ["Brief-to-approval 3 weeks -> 1 week"],
        "estimated_annual_value": 350000, "affected_population": 25, "urgency": "low",
        "data_readiness": 3, "technical_complexity": 2, "change_impact": 2,
        "risk_categories": ["intellectual_property", "hallucination_accuracy"],
    }),
    (Status.submitted.value, 2, {
        "title": "Personalised product recommendations in the mobile app",
        "summary": "Recommend relevant banking products and tips to retail customers in the mobile app based on their behaviour and life events.",
        "business_unit": "Retail Banking - Digital",
        "problem_statement": "Generic in-app offers convert at 0.4%; customers find them irrelevant and opt out of notifications.",
        "target_users": ["Retail customers (3M)", "Digital marketing (15)"],
        "ai_capability": "recommendation", "human_in_the_loop": "none", "interaction_mode": "embedded_in_app",
        "solution_description": "Propensity models and rules engine select the next best offer per customer; marketing controls campaigns and exclusions.",
        "data_sources": ["Customer profiles", "Card transactions", "App events"], "data_sensitivity": "personal_data",
        "source_systems": ["Core banking (Temenos)", "Salesforce Marketing Cloud"], "target_systems": ["Mobile app"],
        "value_drivers": ["revenue_growth", "customer_experience"], "kpis": ["Offer conversion 0.4% -> 1.5%", "Opt-out rate -30%"],
        "estimated_annual_value": 3000000, "affected_population": 3000000, "urgency": "high",
        "data_readiness": 4, "technical_complexity": 3, "change_impact": 2,
        "risk_categories": ["bias_fairness", "privacy"],
    }),
    (Status.draft.value, 3, {
        "title": "Churn prediction for retail customers",
        "summary": "Predict customers likely to close their accounts in the next 90 days and trigger retention actions.",
        "business_unit": "Retail Banking - Digital",
        "problem_statement": "Annual churn is 7% and retention actions start only after the customer has asked to close the account.",
        "target_users": ["Branch advisors (1,200)", "Retention team (20)"],
        "ai_capability": "prediction", "human_in_the_loop": "review_before_action", "interaction_mode": "batch",
        "solution_description": "Monthly churn scores pushed to Salesforce; advisors receive prioritised retention tasks with suggested actions.",
        "data_sources": ["Customer profiles", "Card transactions", "App events", "Complaints"], "data_sensitivity": "personal_data",
        "source_systems": ["Core banking (Temenos)", "Salesforce"], "target_systems": ["Salesforce"],
        "value_drivers": ["revenue_growth", "customer_experience"], "kpis": ["Churn 7% -> 5.5%"],
        "estimated_annual_value": 2200000, "affected_population": 1220, "urgency": "medium",
        "data_readiness": 4, "technical_complexity": 3, "change_impact": 2,
        "dependencies_notes": "Reuses the customer feature store built for product recommendations.",
    }),
    (Status.approved.value, 1, {
        "title": "Enterprise knowledge assistant (internal search)",
        "summary": "A company-wide assistant that answers employee questions from approved internal documentation across SharePoint, Confluence and the intranet, with citations.",
        "business_unit": "Group IT - Workplace",
        "problem_statement": "Employees spend 1.8 hours a day searching for information across 12 repositories; answers are outdated or contradictory.",
        "target_users": ["All employees (25,000)"],
        "ai_capability": "search_rag", "human_in_the_loop": "none", "interaction_mode": "chat",
        "solution_description": "Permission-aware RAG platform with connectors to SharePoint, Confluence and the intranet; exposed in Teams and as an API for domain assistants (HR, IT, compliance).",
        "data_sources": ["SharePoint", "Confluence", "Intranet pages"], "data_sensitivity": "internal",
        "source_systems": ["SharePoint", "Confluence"], "target_systems": ["Microsoft Teams"],
        "value_drivers": ["employee_experience", "cost_reduction", "quality"], "kpis": ["Search time -30%", "Weekly active users 40%"],
        "estimated_annual_value": 5000000, "affected_population": 25000, "urgency": "high",
        "data_readiness": 3, "technical_complexity": 3, "change_impact": 2,
        "eu_ai_act_tier": "minimal_risk", "risk_categories": ["hallucination_accuracy", "security"],
        "build_approach": "build", "estimated_effort": "XL", "teams_involved": ["Data & AI platform", "Workplace IT"],
        "models_considered": ["Claude", "Azure OpenAI GPT-4o", "internal LangGraph platform"],
    }),
    (Status.draft.value, 2, {
        "title": "Meeting notes and action items generator",
        "summary": "Generate structured minutes and action items from Teams meeting transcripts and push tasks to Planner.",
        "business_unit": "Group IT - Workplace",
        "problem_statement": "Minutes are written inconsistently or not at all; action items get lost between meetings.",
        "target_users": ["All employees (25,000)"],
        "ai_capability": "summarization", "human_in_the_loop": "review_before_action", "interaction_mode": "embedded_in_app",
        "solution_description": "After each recorded meeting, generate minutes, decisions and owners; organiser reviews and publishes.",
        "data_sources": ["Teams meeting transcripts"], "data_sensitivity": "internal",
        "source_systems": ["Microsoft Teams"], "target_systems": ["Microsoft Teams", "Microsoft Planner"],
        "value_drivers": ["employee_experience", "speed_time_to_market"], "kpis": ["Meetings with published minutes 20% -> 80%"],
        "estimated_annual_value": 800000, "affected_population": 25000, "urgency": "low",
        "data_readiness": 4, "technical_complexity": 1, "change_impact": 1,
        "build_approach": "buy", "estimated_effort": "S", "models_considered": ["Microsoft 365 Copilot"],
    }),
    (Status.submitted.value, 3, {
        "title": "Code review and documentation assistant for developers",
        "summary": "AI assistant integrated in GitLab that reviews merge requests for security and style issues and generates documentation.",
        "business_unit": "Group IT - Engineering",
        "problem_statement": "Code review takes 2 days on average; 30% of production incidents trace back to issues that a review should have caught.",
        "target_users": ["Developers (900)"],
        "ai_capability": "generation", "human_in_the_loop": "review_before_action", "interaction_mode": "embedded_in_app",
        "solution_description": "On each merge request, an agent comments on risky patterns, missing tests and documentation gaps; developers accept suggestions.",
        "data_sources": ["Source code (GitLab)", "Internal coding standards"], "data_sensitivity": "confidential",
        "source_systems": ["GitLab"], "target_systems": ["GitLab"],
        "value_drivers": ["quality", "speed_time_to_market"], "kpis": ["Review lead time 2 days -> 0.5 day", "Incidents from code defects -25%"],
        "estimated_annual_value": 1500000, "affected_population": 900, "urgency": "medium",
        "data_readiness": 5, "technical_complexity": 2, "change_impact": 2,
        "risk_categories": ["security", "intellectual_property"], "build_approach": "buy",
    }),
    (Status.draft.value, 2, {
        "title": "Branch cash demand forecasting",
        "summary": "Forecast daily cash needs per branch and ATM to optimise cash replenishment logistics.",
        "business_unit": "Retail Banking Operations",
        "problem_statement": "Branches hold 30% excess cash while 4% of ATMs run out monthly; replenishment routes are planned manually.",
        "target_users": ["Cash logistics team (15)", "Branch managers (400)"],
        "ai_capability": "prediction", "human_in_the_loop": "exception_only", "interaction_mode": "batch",
        "solution_description": "Time-series forecasting per site with calendar and local event features; outputs a replenishment plan for the logistics provider.",
        "data_sources": ["ATM transaction logs", "Branch cash positions", "Calendar events"], "data_sensitivity": "internal",
        "source_systems": ["Core banking (Temenos)", "ATM monitoring"], "target_systems": ["Cash logistics portal"],
        "value_drivers": ["cost_reduction"], "kpis": ["Idle cash -25%", "ATM outages -80%"],
        "estimated_annual_value": 1100000, "affected_population": 415, "urgency": "medium",
        "data_readiness": 4, "technical_complexity": 3, "change_impact": 1, "eu_ai_act_tier": "minimal_risk",
    }),
    (Status.submitted.value, 3, {
        "title": "Complaint classification and root-cause analytics",
        "summary": "Classify customer complaints by product, cause and severity, and surface emerging root causes to product owners.",
        "business_unit": "Customer Service - Quality",
        "problem_statement": "40,000 yearly complaints are tagged manually with 25 inconsistent categories; systemic issues are detected months late.",
        "target_users": ["Complaint handlers (80)", "Product owners (40)"],
        "ai_capability": "classification", "human_in_the_loop": "exception_only", "interaction_mode": "batch",
        "solution_description": "LLM classification of complaint text into a governed taxonomy plus weekly clustering of emerging themes with examples.",
        "data_sources": ["Salesforce cases", "Complaint letters (DMS)"], "data_sensitivity": "personal_data",
        "source_systems": ["Salesforce"], "target_systems": ["Salesforce", "Power BI"],
        "value_drivers": ["customer_experience", "compliance", "quality"], "kpis": ["Tagging consistency 60% -> 95%", "Time to detect systemic issue -70%"],
        "estimated_annual_value": 600000, "affected_population": 120, "urgency": "medium",
        "data_readiness": 4, "technical_complexity": 2, "change_impact": 1,
    }),
    (Status.draft.value, 2, {
        "title": "Voice of customer: call sentiment and theme analytics",
        "summary": "Analyse call transcripts for sentiment and recurring themes to feed the customer experience dashboard.",
        "business_unit": "Customer Service - Quality",
        "problem_statement": "Only 1% of calls are quality-monitored; management lacks a view of why customers call and how they feel.",
        "target_users": ["CX analysts (10)", "Call centre managers (40)"],
        "ai_capability": "classification", "human_in_the_loop": "none", "interaction_mode": "batch",
        "solution_description": "Reuse call transcripts to classify reason for call, sentiment and friction points, aggregated weekly in Power BI.",
        "data_sources": ["Call recordings (Genesys)", "Salesforce cases"], "data_sensitivity": "personal_data",
        "source_systems": ["Genesys Cloud", "Salesforce"], "target_systems": ["Power BI"],
        "value_drivers": ["customer_experience", "quality"], "kpis": ["Calls analysed 1% -> 100%"],
        "estimated_annual_value": 400000, "affected_population": 50, "urgency": "low",
        "data_readiness": 3, "technical_complexity": 2, "change_impact": 1,
        "dependencies_notes": "Depends on the speech-to-text pipeline from call summarisation.",
    }),
    (Status.draft.value, 3, {
        "title": "Procurement RFP response drafting",
        "summary": "Draft responses to supplier RFP questionnaires from a library of approved answers and past tenders.",
        "business_unit": "Procurement",
        "problem_statement": "Each RFP response takes 3 weeks and reuses 70% of prior content that is scattered across emails and drives.",
        "target_users": ["Procurement and bid managers (20)"],
        "ai_capability": "generation", "human_in_the_loop": "review_before_action", "interaction_mode": "chat",
        "solution_description": "RAG over approved answer library and past tenders; generates first drafts per question with sources for review.",
        "data_sources": ["Past RFP responses (SharePoint)", "Approved answer library"], "data_sensitivity": "confidential",
        "source_systems": ["SharePoint"], "target_systems": ["SharePoint"],
        "value_drivers": ["speed_time_to_market", "cost_reduction"], "kpis": ["RFP response time 3 weeks -> 1 week"],
        "estimated_annual_value": 250000, "affected_population": 20, "urgency": "low",
        "data_readiness": 3, "technical_complexity": 2, "change_impact": 1,
    }),
    (Status.qualified.value, 1, {
        "title": "Internal AI platform: LLM gateway and agent runtime",
        "summary": "A shared platform providing a governed LLM gateway (multi-provider), RAG building blocks, agent runtime (LangGraph), evaluation and observability for all AI use cases.",
        "business_unit": "Group IT - Data & AI Platform",
        "problem_statement": "Each team integrates LLM providers independently, duplicating security reviews, cost controls, evaluation tooling and monitoring.",
        "target_users": ["AI engineering teams (12 teams)", "Model risk management (8)"],
        "ai_capability": "agentic_workflow", "human_in_the_loop": "none", "interaction_mode": "api",
        "solution_description": "Central gateway with provider abstraction, PII filtering, quota and cost tracking, prompt registry, evaluation harness and tracing. Domain assistants are deployed on this runtime.",
        "data_sources": ["Prompt and completion logs", "Evaluation datasets"], "data_sensitivity": "confidential",
        "source_systems": ["Azure OpenAI", "Anthropic API", "Internal Kubernetes platform"], "target_systems": ["Internal Kubernetes platform"],
        "value_drivers": ["cost_reduction", "compliance", "speed_time_to_market"], "kpis": ["Time to deploy a new assistant 3 months -> 3 weeks", "LLM spend visibility 100%"],
        "estimated_annual_value": 3000000, "affected_population": 150, "urgency": "critical",
        "data_readiness": 4, "technical_complexity": 4, "change_impact": 3,
        "eu_ai_act_tier": "gpai_obligations_only", "build_approach": "build", "estimated_effort": "XL",
        "teams_involved": ["Data & AI platform", "Security", "Model risk management"],
        "models_considered": ["Claude", "Azure OpenAI GPT-4o", "Mistral Large", "internal LangGraph platform"],
    }),
    (Status.draft.value, 2, {
        "title": "Employee onboarding assistant",
        "summary": "A chatbot guiding new joiners through their first weeks: IT setup, mandatory trainings, HR formalities and who to contact.",
        "business_unit": "Human Resources",
        "problem_statement": "New joiners ask the same 50 questions to their manager and HR; onboarding satisfaction is 6.5/10.",
        "target_users": ["New joiners (2,000 / year)", "Managers"],
        "ai_capability": "conversational_agent", "human_in_the_loop": "none", "interaction_mode": "chat",
        "solution_description": "RAG assistant over onboarding guides and HR policies in Teams, with checklists and escalation to HR.",
        "data_sources": ["HR policy documents (SharePoint)", "Onboarding guides", "ServiceNow IT catalog"], "data_sensitivity": "internal",
        "source_systems": ["SharePoint", "ServiceNow"], "target_systems": ["Microsoft Teams"],
        "value_drivers": ["employee_experience"], "kpis": ["Onboarding satisfaction 6.5 -> 8.5"],
        "estimated_annual_value": 200000, "affected_population": 2000, "urgency": "low",
        "data_readiness": 3, "technical_complexity": 2, "change_impact": 1,
    }),
]


DEMO_TAG = "demo"
DEMO_OWNER_EMAIL = "demo-owner@smartuc.local"
DEMO_GUEST_EMAIL = "demo-guest@smartuc.local"


async def ensure_demo_guest(session: AsyncSession) -> User:
    """Shared read-only visitor account used by the public demo (reviewer visibility, writes refused)."""
    guest = await session.scalar(select(User).where(User.email == DEMO_GUEST_EMAIL))
    if not guest:
        guest = User(email=DEMO_GUEST_EMAIL, full_name="Demo visitor (read-only)", role=Role.reviewer.value,
                     password_hash=hash_password(secrets.token_urlsafe(32)), is_active=True, is_demo_guest=True)
        session.add(guest)
        await session.flush()
    return guest


async def load_demo(session: AsyncSession, validator: User, *, owners: list[User] | None = None,
                    reviewer: User | None = None) -> int:
    """Create the demo portfolio, tagged `demo`. Works in production (no demo accounts, no LLM needed):
    use cases are owned by an inactive technical user so the four-eyes rule holds for `validator`."""
    template = await get_default_template(session)
    if owners is None:
        owner = await session.scalar(select(User).where(User.email == DEMO_OWNER_EMAIL))
        if not owner:
            owner = User(email=DEMO_OWNER_EMAIL, full_name="Demo requester (sample data)", role=Role.requester.value,
                         password_hash=hash_password(secrets.token_urlsafe(24)), is_active=False)
            session.add(owner)
            await session.flush()
        owners = [owner]
    reviewer = reviewer or validator
    created = 0
    for status, owner_idx, fields in DEMO:
        fields = dict(fields)
        tier = fields.pop("eu_ai_act_tier", None)
        rationale = fields.pop("eu_ai_act_rationale", "")
        uc = await create_use_case(session, template, fields, owners[owner_idx % len(owners)], status=status,
                                   source="manual", tags=[DEMO_TAG])
        if tier:  # governance record; validator is never the owner (four-eyes)
            await validate_risk(session, uc, validator, tier, rationale or "Seeded validation")
        path = ["draft", "submitted", "qualified", "approved", "in_build", "in_production"]
        if status in path:
            for a, b in zip(path, path[1:]):
                if path.index(b) <= path.index(status):
                    session.add(Decision(use_case_id=uc.id, from_status=a, to_status=b,
                                         comment="Seeded decision (demo)", decided_by_id=reviewer.id))
        created += 1
    await session.commit()
    stats = await analysis.analyze_portfolio(session, use_llm=False)  # structural + lexical, no LLM needed
    log.info("Loaded %d demo use cases; analysis: %s", created, stats)
    return created


async def remove_demo(session: AsyncSession) -> int:
    """Hard-delete every use case tagged `demo` and its dependent rows. Audit events are kept."""
    from sqlalchemy import delete, or_, update

    from .models import IntakeSession, Relation, RiskValidation, UseCaseEntity, UseCaseVersion

    ucs = (await session.scalars(select(UseCase))).all()
    ids = [u.id for u in ucs if DEMO_TAG in (u.tags or [])]
    if not ids:
        return 0
    await session.execute(update(IntakeSession).where(IntakeSession.use_case_id.in_(ids)).values(use_case_id=None))
    for model in (Relation,):
        await session.execute(delete(model).where(or_(model.source_id.in_(ids), model.target_id.in_(ids))))
    for model in (UseCaseEntity, UseCaseVersion, Decision, RiskValidation):
        await session.execute(delete(model).where(model.use_case_id.in_(ids)))
    await session.execute(delete(UseCase).where(UseCase.id.in_(ids)))
    await session.commit()
    return len(ids)


async def seed_demo(session: AsyncSession) -> None:
    """Dev-only startup seed: demo accounts (password demo1234) + the demo portfolio."""
    users = []
    for email, name, role in USERS:
        u = await session.scalar(select(User).where(User.email == email))
        if not u:
            u = User(email=email, full_name=name, role=role, password_hash=hash_password("demo1234"))
            session.add(u)
        users.append(u)
    await session.flush()
    await load_demo(session, users[0], owners=users, reviewer=users[0])
