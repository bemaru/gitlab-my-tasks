import os
import re

import requests
import urllib3
from dotenv import load_dotenv

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# .env 파일에서 환경변수 로드
load_dotenv()

GITLAB_URL = os.getenv("GITLAB_URL")
PRIVATE_TOKEN = os.getenv("PRIVATE_TOKEN")
PROJECT_ID = int(os.getenv("PROJECT_ID"))
AUTHOR_ID = os.getenv("AUTHOR_ID")

headers = {"PRIVATE-TOKEN": PRIVATE_TOKEN}


def get_assigned_issues(project_id, assignee_id):
    url = f"{GITLAB_URL}/api/v4/projects/{project_id}/issues"
    params = {
        "assignee_id": assignee_id,
        "per_page": 100,
        "order_by": "created_at",
        "sort": "asc",
        "state": "all",
    }
    response = requests.get(url, headers=headers, params=params, verify=False)
    response.raise_for_status()
    return response.json()


def get_issue_links(project_id, issue_iid):
    url = f"{GITLAB_URL}/api/v4/projects/{project_id}/issues/{issue_iid}/links"
    response = requests.get(url, headers=headers, verify=False)
    if response.status_code == 404:
        return []
    response.raise_for_status()
    return response.json()


def get_issue_tasks(project_id, issue_iid):
    url = f"{GITLAB_URL}/api/v4/projects/{project_id}/issues/{issue_iid}/tasks"
    response = requests.get(url, headers=headers, verify=False)
    if response.status_code == 404:
        return []  # tasks 엔드포인트 미지원 시 빈 리스트 반환
    response.raise_for_status()
    return response.json()


def get_my_todos():
    url = f"{GITLAB_URL}/api/v4/todos"
    response = requests.get(url, headers=headers, verify=False)
    response.raise_for_status()
    return response.json()


def parse_tasks_from_description(description):
    if not description:
        return []
    # 마크다운 체크박스 패턴 파싱
    pattern = r"^- \[( |x)\] (.+)$"
    tasks = []
    for line in description.splitlines():
        match = re.match(pattern, line.strip())
        if match:
            checked = match.group(1) == "x"
            text = match.group(2)
            tasks.append((checked, text))
    return tasks


def build_issue_tree(issues):
    # id로 빠른 lookup
    issues_by_id = {issue["id"]: issue for issue in issues}
    iid_to_id = {issue["iid"]: issue["id"] for issue in issues}
    # parent-child 매핑 (TASK의 parent 필드)
    children_map = {}
    for issue in issues:
        parent = issue.get("parent")
        if parent and parent.get("id") in issues_by_id:
            children_map.setdefault(parent["id"], []).append(issue)
    # Issue Links 기반 parent-child 매핑
    for issue in issues:
        # TASK가 아닌 경우만 links 조회
        if issue.get("type", "issue") == "issue":
            links = get_issue_links(PROJECT_ID, issue["iid"])
            for link in links:
                # link_type이 blocks인 경우만 자식으로 간주
                if link.get("link_type") == "blocks":
                    linked_id = iid_to_id.get(link["iid"])
                    if linked_id and linked_id != issue["id"]:
                        children_map.setdefault(issue["id"], []).append(
                            issues_by_id[linked_id]
                        )
    # 루트 이슈(부모가 없는 이슈)
    root_issues = []
    for issue in issues:
        parent = issue.get("parent")
        is_child = False
        if parent and parent.get("id") in issues_by_id:
            is_child = True
        # links 기반으로도 자식이면 제외
        for siblings in children_map.values():
            if issue in siblings:
                is_child = True
        if not is_child:
            root_issues.append(issue)
    return issues_by_id, children_map, root_issues


def print_issue_tree(issue, children_map, indent=0):
    prefix = "    " * indent + "- "
    issue_type = issue.get("type", "issue").upper()
    print(
        f"{prefix}[{issue_type}] {issue['title']} (#{issue['iid']}) | 상태: {issue['state']}"
    )
    # 마크다운 체크리스트도 출력(참고용)
    if indent == 0:
        tasks = parse_tasks_from_description(issue.get("description", ""))
        for checked, text in tasks:
            status = "[x]" if checked else "[ ]"
            print(f"{'    ' * (indent+1)}{status} {text}")
    # 자식 이슈/태스크 출력
    for child in children_map.get(issue["id"], []):
        print_issue_tree(child, children_map, indent + 1)


def query_issue_tree_graphql(project_full_path, issue_iid):
    url = f"{GITLAB_URL}/api/graphql"
    query = f"""
    query {{
      project(fullPath: "{project_full_path}") {{
        issues(iid: "{issue_iid}") {{
          nodes {{
            iid
            title
            state
            issueType
            workItemType {{ name }}
            descendants {{
              nodes {{
                iid
                title
                state
                issueType
                workItemType {{ name }}
              }}
            }}
          }}
        }}
      }}
    }}
    """
    response = requests.post(
        url,
        headers={"PRIVATE-TOKEN": PRIVATE_TOKEN},
        json={"query": query},
        verify=False,
    )
    response.raise_for_status()
    return response.json()


def print_workitem_hierarchy(workitem, indent=0, lines=None):
    if lines is None:
        lines = []
    prefix = "    " * indent + "- "
    wtype = workitem.get("workItemType", {}).get("name", "?")
    title = workitem.get("title", "")
    iid = workitem.get("iid", "")
    state = workitem.get("state", "")
    created_at = workitem.get("createdAt", "").split("T")[0]  # YYYY-MM-DD 형식으로 변환
    line = f"{prefix}[{wtype}] #{iid} | {state} | {created_at} | {title}"
    print(line)
    lines.append(line)
    # HIERARCHY 위젯에서 children 재귀 출력
    widgets = workitem.get("widgets", [])
    for widget in widgets:
        if widget.get("type") == "HIERARCHY":
            children = widget.get("children", {}).get("nodes", [])
            for child in children:
                print_workitem_hierarchy(child, indent + 1, lines=lines)
    return lines


def get_all_issue_gids(project_full_path, page_size=100):
    url = f"{GITLAB_URL}/api/graphql"
    query = f"""
    query {{
      project(fullPath: "{project_full_path}") {{
        issues(first: {page_size}) {{
          nodes {{
            id
            iid
            title
            state
          }}
        }}
      }}
    }}
    """
    response = requests.post(
        url,
        headers={"PRIVATE-TOKEN": PRIVATE_TOKEN},
        json={"query": query},
        verify=False,
    )
    response.raise_for_status()
    data = response.json()
    return [node["id"] for node in data["data"]["project"]["issues"]["nodes"]]


def fetch_workitem_hierarchy(issue_gid, page_size=100):
    url = f"{GITLAB_URL}/api/graphql"
    query = """
    query workItemTreeQuery($id: WorkItemID!, $pageSize: Int = 100, $endCursor: String) {
      workItem(id: $id) {
        ...WorkItemHierarchy
      }
    }
    fragment WorkItemHierarchy on WorkItem {
      id
      iid
      title
      state
      createdAt
      workItemType { name }
      widgets {
        type
        ... on WorkItemWidgetHierarchy {
          type
          hasChildren
          children(first: $pageSize, after: $endCursor) {
            nodes {
              id
              iid
              title
              state
              createdAt
              workItemType { name }
              widgets {
                type
                ... on WorkItemWidgetHierarchy {
                  type
                  hasChildren
                  children(first: $pageSize, after: $endCursor) {
                    nodes {
                      id
                      iid
                      title
                      state
                      createdAt
                      workItemType { name }
                    }
                  }
                }
              }
            }
          }
        }
      }
    }
    """
    variables = {"id": issue_gid, "pageSize": page_size, "endCursor": ""}
    response = requests.post(
        url,
        headers={"PRIVATE-TOKEN": PRIVATE_TOKEN},
        json={"query": query, "variables": variables},
        verify=False,
    )
    response.raise_for_status()
    data = response.json()
    return data["data"]["workItem"]


def get_my_issue_gids(project_full_path, my_username, page_size=100):
    url = f"{GITLAB_URL}/api/graphql"
    query = f"""
    query {{
      project(fullPath: "{project_full_path}") {{
        issues(first: {page_size}, assigneeUsernames: ["{my_username}"]) {{
          nodes {{
            id
            iid
            title
            state
          }}
        }}
      }}
    }}
    """
    response = requests.post(
        url,
        headers={"PRIVATE-TOKEN": PRIVATE_TOKEN},
        json={"query": query},
        verify=False,
    )
    response.raise_for_status()
    data = response.json()
    return [node["id"] for node in data["data"]["project"]["issues"]["nodes"]]


if __name__ == "__main__":
    project_full_path = os.getenv("PROJECT_FULL_PATH") or "epp/edr"
    my_username = os.getenv("GITLAB_USERNAME")
    print("[GraphQL HIERARCHY] 나에게 할당된 이슈/태스크 트리:")
    output_lines = ["[GraphQL HIERARCHY] 나에게 할당된 이슈/태스크 트리:"]
    my_gids = get_my_issue_gids(project_full_path, my_username, page_size=100)
    print(f"총 {len(my_gids)}건")
    output_lines.append(f"총 {len(my_gids)}건")
    for gid in my_gids:
        workitem = fetch_workitem_hierarchy(gid, page_size=100)
        print_workitem_hierarchy(workitem, lines=output_lines)
    with open("my_gitlab_tasks.txt", "w", encoding="utf-8") as f:
        f.write("\n".join(output_lines))
