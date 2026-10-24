"""
Judge prompts for the ODR evals, copied from published benchmarks (only output-format lines adapted).

- UMBRELA: castorini/umbrela, src/umbrela/prompts/prompt_templates/qrel_zeroshot_bing.yaml (Apache-2.0).
  Reproduction of the Bing relevance assessor used for TREC 2024 RAG. Adaptation: the final
  "##final score:" line asks for the `final_score` JSON field instead (structured outputs).
- RACE: Ayanami0730/deep_research_bench, prompt/score_prompt_en.py `generate_merged_score_prompt`
  (Apache-2.0). Used verbatim; the JSON shape it asks for is enforced with a per-task schema.
- FACT: deep_research_bench utils/extract.py and utils/validate.py `prompt_template_en` (Apache-2.0).
  Used verbatim; the JSON shape is enforced with a schema.

Regenerate with the upstream files rather than editing by hand, so the text stays identical.
Format placeholders: UMBRELA {query} {passage}; RACE {task_prompt} {article_1} {article_2} {criteria_list};
FACT extract {report_text}; FACT validate {reference} {statements}.
"""

UMBRELA_PROMPT = """Given a query and a passage, you must provide a score on an integer scale of 0 to 3 with the following meanings:
0 = represent that the passage has nothing to do with the query, 
1 = represents that the passage seems related to the query but does not answer it, 
2 = represents that the passage has some answer for the query, but the answer may be a bit unclear, or hidden amongst extraneous information and 
3 = represents that the passage is dedicated to the query and contains the exact answer.

Important Instruction: Assign category 1 if the passage is somewhat related to the topic but not completely, category 2 if passage presents something very important related to the entire topic but also has some extra information and category 3 if the passage only and entirely refers to the topic. If none of the above satisfies give it category 0.

Query: {query}
Passage: {passage}

Split this problem into steps:
Consider the underlying intent of the search.
Measure how well the content matches a likely intent of the query (M).
Measure how trustworthy the passage is (T).
Consider the aspects above and the relative importance of each, and decide on a final score (O). Final score must be an integer value only.
Do not provide any code in result. Provide the final score O in the `final_score` field, without providing any reasoning."""

RACE_MERGED_SCORE_PROMPT = """
<system_role>You are a strict, meticulous, and objective research article evaluation expert. You excel at using specific assessment criteria to deeply compare two articles on the same task, providing precise scores and clear justifications.</system_role>

<user_prompt>
**Task Background**
There is a deep research task, and you need to evaluate two research articles written for this task. We will assess the articles across four dimensions: Comprehensiveness, Insight, Instruction Following, and Readability. The content is as follows:
<task>
"{task_prompt}"
</task>

**Articles to Evaluate**
<article_1>
"{article_1}"
</article_1>

<article_2>
"{article_2}"
</article_2>

**Evaluation Criteria**
Now, you need to evaluate and compare these two articles based on the following **evaluation criteria list**, providing comparative analysis and scoring each on a scale of 0-10. Each criterion includes an explanation, please understand carefully.

<criteria_list>
{criteria_list}
</criteria_list>

<Instruction>
**Your Task**
Please strictly evaluate and compare `<article_1>` and `<article_2>` based on **each criterion** in the `<criteria_list>`. You need to:
1.  **Analyze Each Criterion**: Consider how each article fulfills the requirements of each criterion.
2.  **Comparative Evaluation**: Analyze how the two articles perform on each criterion, referencing the content and criterion explanation.
3.  **Score Separately**: Based on your comparative analysis, score each article on each criterion (0-10 points).

**Scoring Rules**
For each criterion, score both articles on a scale of 0-10 (continuous values). The score should reflect the quality of performance on that criterion:
*   0-2 points: Very poor performance. Almost completely fails to meet the criterion requirements.
*   2-4 points: Poor performance. Minimally meets the criterion requirements with significant deficiencies.
*   4-6 points: Average performance. Basically meets the criterion requirements, neither good nor bad.
*   6-8 points: Good performance. Largely meets the criterion requirements with notable strengths.
*   8-10 points: Excellent/outstanding performance. Fully meets or exceeds the criterion requirements.

**Output Format Requirements**
Please **strictly** follow the `<output_format>` below for each criterion evaluation. **Do not include any other unrelated content, introduction, or summary**. Start with "Standard 1" and proceed sequentially through all criteria:
</Instruction>

<output_format>
{{
    "comprehensiveness": [
        {{
            "criterion": [Text content of the first comprehensiveness evaluation criterion],
            "analysis": [Comparative analysis],
            "article_1_score": [Continuous score 0-10],
            "article_2_score": [Continuous score 0-10]
}},
{{
            "criterion": [Text content of the second comprehensiveness evaluation criterion],
            "analysis": [Comparative analysis],
            "article_1_score": [Continuous score 0-10],
            "article_2_score": [Continuous score 0-10]
        }},
        ...
    ],
    "insight": [
        {{
            "criterion": [Text content of the first insight evaluation criterion],
            "analysis": [Comparative analysis],
            "article_1_score": [Continuous score 0-10],
            "article_2_score": [Continuous score 0-10]
        }},
        ...
    ],
    ...
}}
</output_format>

Now, please evaluate the two articles based on the research task and criteria, providing detailed comparative analysis and scores according to the requirements above. Ensure your output follows the specified `<output_format>` and that the JSON format is parsable, with all characters that might cause JSON parsing errors properly escaped.
</user_prompt>
"""

FACT_EXTRACT_PROMPT = """You will be provided with a research report. The body of the report will contain some citations to references.

Citations in the main text may appear in the following forms:
1. A segment of text + space + number, for example: "Li Qiang constructed a socioeconomic status index (SES) based on income, education, and occupation, dividing society into 7 levels 15"
2. A segment of text + [number], for example: "Li Qiang constructed a socioeconomic status index (SES) based on income, education, and occupation, dividing society into 7 levels[15]"
3. A segment of text + [number†(some line numbers, etc.)], for example: "Li Qiang constructed a socioeconomic status index (SES) based on income, education, and occupation, dividing society into 7 levels[15†L10][5L23][7†summary]"
4. [Citation Source](Citation Link), for example: "According to [ChinaFile: A Guide to Social Class in Modern China](https://www.chinafile.com/reporting-opinion/media/guide-social-class-modern-china)'s classification, Chinese society can be divided into nine strata"

Please identify **all** instances where references are cited in the main text, and extract (fact, ref_idx, url) triplets. When extracting, pay attention to the following:
1. Since these facts will need to be verified later, you may need to look for some context before and after the citation to ensure that the fact is complete and understandable, rather than just a simple phrase or short expression.
2. If a fact cites multiple references, then it should correspond to two triplets: (fact, ref_idx_1, url_1) and (fact, ref_idx_2, url_2).
3. For the third form of citation (i.e., where the citation source and link appear directly in the text), the ref_idx should be uniformly set to 0.
4. If the main text does not specify the exact location of the citation (for example, only the reference list is listed at the end of the article, without specifying the citation point in the text), please return an empty list.

You should return a JSON list format, where each item in the list is a triplet, for example:
[
    {{
        "fact": "Text segment from the original document. Note that Chinese quotation marks should use full-width marks. And add a single backslash before the English quotation mark to make it a readable for python json module.",
        "ref_idx": "The index of the cited reference in the reference list for this text segment.",
        "url": "The URL of the cited reference for this text segment (extracted from the reference list at the end of the research report or from the parentheses at the citation point)."
    }}
]

Here is the main text of the research report:
{report_text}

Please begin the extraction now. Output only the JSON list directly, without any chitchat or explanations."""

FACT_VALIDATE_PROMPT = """You will be provided with a reference and some statements. Please determine whether each statement is 'supported', 'unsupported', or 'unknown' with respect to the reference. Please note:
First, assess whether the reference contains any valid content. If the reference contains no valid information, such as a 'page not found' message, then all statements should be considered 'unknown'.
If the reference is valid, for a given statement: if the facts or data it contains can be found entirely or partially within the reference, it is considered 'supported' (data accepts rounding); if all facts and data in the statement cannot be found in the reference, it is considered 'unsupported'.

You should return the result in a JSON list format, where each item in the list contains the statement's index and the judgment result, for example:
[
    {{
        "idx": 1,
        "result": "supported"
    }},
    {{
        "idx": 2,
        "result": "unsupported"
    }}
]

Below are the reference and statements:
<reference>
{reference}
</reference>

<statements>
{statements}
</statements>

Begin the assessment now. Output only the JSON list, without any conversational text or explanations."""
