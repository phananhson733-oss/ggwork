/**
 * About GGWork markdown content. Inlined to avoid raw-loader dependency
 * (Turbopack cannot resolve raw-loader for .md imports).
 */
import { APP_NAME } from "@/core/brand";
import { APP_VERSION } from "@/version";

export const aboutMarkdown = `# About ${APP_NAME} ${APP_VERSION}

${APP_NAME} is a workbench for short-drama selection. Its agents and skills search the web, analyze data and organize drama-selection data, and can turn the results into documents, slides and web pages.

---

## License

${APP_NAME} is built on the open-source DeerFlow project, which is distributed under the **MIT License**.
`;
