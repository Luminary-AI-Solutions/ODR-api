# Eval inputs: 20 DeepResearch Bench tasks

Source: [DeepResearch Bench](https://github.com/Ayanami0730/deep_research_bench) (Apache-2.0), English half, topic-stratified with `evals/select_tasks.py --n 20 --seed 0`.
Each task ships with its own RACE criteria + dimension weights (`criteria.jsonl`) and a reference report (`reference.jsonl`).

| id | topic | prompt words | RACE criteria | reference report words |
|---|---|---|---|---|
| 52 | Finance & Business | 13 | 23 | 5,529 |
| 53 | Finance & Business | 7 | 26 | 9,209 |
| 55 | Finance & Business | 54 | 26 | 14,996 |
| 58 | Science & Technology | 92 | 28 | 8,289 |
| 59 | Science & Technology | 20 | 26 | 7,374 |
| 62 | Science & Technology | 54 | 27 | 11,766 |
| 66 | Software Development | 30 | 23 | 5,310 |
| 68 | Software Development | 71 | 27 | 8,431 |
| 72 | Education & Jobs | 47 | 25 | 9,029 |
| 74 | Education & Jobs | 27 | 25 | 10,501 |
| 75 | Health | 38 | 25 | 12,756 |
| 77 | Health | 11 | 25 | 8,213 |
| 80 | Literature | 76 | 26 | 5,152 |
| 81 | History | 53 | 25 | 9,081 |
| 83 | Hardware | 79 | 23 | 9,552 |
| 86 | Industrial | 56 | 23 | 11,267 |
| 87 | Art & Design | 24 | 25 | 9,181 |
| 90 | Crime & Law | 58 | 26 | 9,619 |
| 91 | Entertainment | 67 | 22 | 9,976 |
| 99 | Fashion & Beauty | 36 | 26 | 10,152 |

Topic counts: Finance & Business 3, Science & Technology 3, Software Development 2, Education & Jobs 2, Health 2, Literature 1, History 1, Hardware 1, Industrial 1, Art & Design 1, Crime & Law 1, Entertainment 1, Fashion & Beauty 1

## 52 · Finance & Business

````text
What are the investment philosophies of Duan Yongping, Warren Buffett, and Charlie Munger?
````

## 53 · Finance & Business

````text
Researching how the world's wealthiest governments invest.
````

## 55 · Finance & Business

````text
While the market features diverse quantitative strategies like multi-factor and high-frequency trading, it lacks a single, standardized benchmark for assessing their performance across multiple dimensions such as returns, risk, and adaptability to market conditions. Could we develop a general yet rigorous evaluation framework to enable accurate comparison and analysis of various advanced quant strategies?
````

## 58 · Science & Technology

````text
Exploring Horizontal Gene Transfer (HGT) in Plants and animals (ie Non-Microbial Systems)
You could examine instances of horizontal gene transfer in eukaryotes—particularly plants and animals—and evaluate the evolutionary significance of these transfers. Its very rare and therefore must have a really interesting reason behind this adaptation!
Especially as this horizontal gene transfer has been well -studied in microbial systems, but not in plants and animals (this is a relatively new discovery).  Understanding  how commonly genes move between eukaryotic species and whether these transfers confer benefits would be really interesting to find out
````

## 59 · Science & Technology

````text
In ecology, how do birds achieve precise location and direction navigation during migration? What cues and disturbances influence this process?
````

## 62 · Science & Technology

````text
What are the most effective approaches to scaling ion trap quantum computing from small-scale demonstration projects to large-scale systems capable of solving real-world problems? This research should investigate the various proposed scaling strategies, assess their feasibility, and evaluate which approaches are most likely to succeed based on current technological advancements and practical implementation challenges.
````

## 66 · Software Development

````text
Which Obsidian plugins can effectively replicate Notion's multi-view database functionality (including Table, Kanban, Calendar, and List views)? Please provide a detailed comparison of the strengths and weaknesses of these plugins.
````

## 68 · Software Development

````text
I need to dynamically adjust Kubernetes (K8S) cluster node counts based on fluctuating business request volumes, ensuring resources are scaled up proactively before peak loads and scaled down promptly during troughs. The standard Cluster Autoscaler (CA) isn't suitable as it relies on pending pods and might not fit non-elastic node group scenarios. What are effective implementation strategies, best practices, or existing projects that address predictive or scheduled autoscaling for K8S nodes?
````

## 72 · Education & Jobs

````text
Please write a literature review on the restructuring impact of Artificial Intelligence (AI) on the labor market. Focus on how AI, as a key driver of the Fourth Industrial Revolution, is causing significant disruptions and affecting various industries. Ensure the review only cites high-quality, English-language journal articles.
````

## 74 · Education & Jobs

````text
Please conduct a study and prepare a report on the 'Construction and Application of a Sports Intelligent Tutoring and Learning Guidance System Driven by Multimodal Data Fusion.'
````

## 75 · Health

````text
Could the rapeutic interventions aimed at modulating plasma metal ion concentrations represent effective preventive or therapeutic strategies against cardiovascular diseases? What types of interventions—such as supplementation—have been proposed, and is there clinical evidence supporting their feasibility and efficacy?
````

## 77 · Health

````text
What is the role of need for closure on misinformation acceptance?
````

## 80 · Literature

````text
Please investigate the influence of mass media on language, specifically the queer community of Japan. I am trying to see if the consumption of shoujo manga by queer Japanese young adults affects their pronoun use and sentence ending particles. Both grammatical categories are gendered in Japanese and a distinct pattern emerges in shoujo manga compared to majority use in society, so observing a minority group would give insight into the effect of media in personal expression.
````

## 81 · History

````text
Write an analysis exploring how historical narratives are being reinterpreted through contemporary political and social lenses. Focus on areas like ideologisation of history, instrumentalisation of the past and efforts to reclaim silenced narratives. Analyze how commemorative practices shape historiography and how historical memory serves current agendas. Please provide relevant examples and scholarly perspectives.
````

## 83 · Hardware

````text
Acting as a senior hardware product manager, conduct in-depth research on tablet-style devices used for payments or SaaS applications. Your report should: 1) List major manufacturers, specific device models, and their configurations. 2) Include images of these devices. 3) Analyze the primary use cases and scenarios where these devices are deployed. 4) Investigate the market penetration, common usage scenarios, typical price ranges, and estimated installed base for such devices across different regions (North America, Japan/Korea, Southeast Asia, South America).
````

## 86 · Industrial

````text
Conduct a research report on the manufacturing technology options for hollow motor shafts used in New Energy Vehicle (NEV) electric drive units. List all current forming techniques, compare them based on criteria such as suitable materials, cost-effectiveness, required subsequent processing steps, and other relevant factors. Finally, identify the most suitable manufacturing routes for this specific application.
````

## 87 · Art & Design

````text
Are AI fashion design tools leading to creative homogenization in the industry? How can the copyright disputes between independent designers and algorithms be resolved?
````

## 90 · Crime & Law

````text
Analyze the complex issue of liability allocation in accidents involving vehicles with advanced driver-assistance systems (ADAS) operating in a shared human-machine driving context. Your analysis should integrate technical principles of ADAS, existing legal frameworks, and relevant case law to systematically examine the boundaries of responsibility between the driver and the system. Conclude with proposed regulatory guidelines or recommendations.
````

## 91 · Entertainment

````text
I would like a detailed analysis of the Saint Seiya franchise (anime/manga). The analysis should be structured around the different classes of armor (Cloths, Scales, Surplices, God Robes, etc.), such as Bronze Saints, Silver Saints, Gold Saints, Marina Generals, Specters, God Warriors, etc. For each significant character within these categories, provide details on their power level, signature techniques, key appearances/story arcs, and final outcome/fate within the series.
````

## 99 · Fashion & Beauty

````text
Research the current applications and recent scientific advancements of various light-based therapies (e.g., laser, IPL, LED) in aesthetic medicine for treating conditions such as photoaging, promoting skin whitening/brightening, and reducing hyperpigmentation (like age spots or melasma).
````
