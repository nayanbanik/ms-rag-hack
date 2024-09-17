import { Example } from "./Example";

import styles from "./Example.module.css";

// Updated examples reflecting academic research use case for RAG
const DEFAULT_EXAMPLES: string[] = [
    "What are the main challenges in Retrieval-Augmented Generation?",
    "How does RAG compare to traditional retrieval methods?",
    "Can RAG models be used for summarizing academic papers?"
];

const GPT4V_EXAMPLES: string[] = [
    "What does this graph in the paper represent?",
    "Does the chart indicate a significant trend?",
    "How do the figures support the research findings?"
];

interface Props {
    onExampleClicked: (value: string) => void;
    useGPT4V?: boolean;
}

export const ExampleList = ({ onExampleClicked, useGPT4V }: Props) => {
    return (
        <ul className={styles.examplesNavList}>
            {(useGPT4V ? GPT4V_EXAMPLES : DEFAULT_EXAMPLES).map((question, i) => (
                <li key={i}>
                    <Example text={question} value={question} onClick={onExampleClicked} />
                </li>
            ))}
        </ul>
    );
};
