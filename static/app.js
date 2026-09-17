const button = document.querySelector("#submit");
const question = document.querySelector("#question");
const progress = document.querySelector("#progress");
const events = document.querySelector("#events");
const answer = document.querySelector("#answer");
const errorPanel = document.querySelector("#error");
const charts = document.querySelector("#charts");
const chartColors = ["#167d6a", "#f09b46", "#5c78d6", "#b25aa5"];

const labels = {
  "task.submitted": "请求已受理",
  "task.accepted": "后台任务已启动",
  "stage.started": "开始执行阶段",
  "stage.completed": "阶段执行完成",
  "evidence.ready": "查询证据已就绪",
  "chart.ready": "图表数据已就绪",
  "answer.ready": "分析结论已生成",
  "task.completed": "任务完成"
};

function addEvent(type, payload) {
  const item = document.createElement("li");
  const stage = payload.data?.stage ? `：${payload.data.stage}` : "";
  item.textContent = `${labels[type] || type}${stage}`;
  events.appendChild(item);
}

function svgNode(name, attributes = {}, text = "") {
  const node = document.createElementNS("http://www.w3.org/2000/svg", name);
  Object.entries(attributes).forEach(([key, value]) => node.setAttribute(key, value));
  if (text) node.textContent = text;
  return node;
}

function renderChart(spec) {
  const card = document.createElement("article");
  card.className = "chart-card";
  const title = document.createElement("h3");
  title.textContent = spec.title;
  const meta = document.createElement("p");
  meta.className = "chart-meta";
  meta.textContent = `单位：${spec.unit} · 证据：${spec.evidence_ids.join("、") || "—"}`;
  const width = 760;
  const height = 300;
  const margin = {top: 20, right: 20, bottom: 58, left: 56};
  const plotWidth = width - margin.left - margin.right;
  const plotHeight = height - margin.top - margin.bottom;
  const svg = svgNode("svg", {viewBox: `0 0 ${width} ${height}`, role: "img"});
  const values = spec.series.flatMap(item => item.values);
  const maxValue = Math.max(...values, 1);
  const groupWidth = plotWidth / Math.max(spec.categories.length, 1);

  for (let step = 0; step <= 4; step += 1) {
    const y = margin.top + plotHeight - plotHeight * step / 4;
    svg.append(
      svgNode("line", {
        x1: margin.left, y1: y, x2: width - margin.right, y2: y,
        stroke: "#dbe8e5", "stroke-width": 1
      }),
      svgNode("text", {
        x: margin.left - 8, y: y + 4, "text-anchor": "end",
        fill: "#728782", "font-size": 11
      }, (maxValue * step / 4).toFixed(1))
    );
  }

  spec.categories.forEach((category, index) => {
    const x = margin.left + groupWidth * (index + 0.5);
    svg.appendChild(svgNode("text", {
      x, y: height - 28, "text-anchor": "middle",
      fill: "#526b66", "font-size": 11
    }, category.length > 10 ? `${category.slice(0, 10)}…` : category));
  });

  if (spec.chart_type === "line") {
    spec.series.forEach((series, seriesIndex) => {
      const points = series.values.map((value, index) => {
        const x = margin.left + groupWidth * (index + 0.5);
        const y = margin.top + plotHeight * (1 - value / maxValue);
        return `${x},${y}`;
      });
      svg.appendChild(svgNode("polyline", {
        points: points.join(" "), fill: "none",
        stroke: chartColors[seriesIndex], "stroke-width": 3
      }));
      points.forEach(point => {
        const [cx, cy] = point.split(",");
        svg.appendChild(svgNode("circle", {
          cx, cy, r: 4, fill: chartColors[seriesIndex]
        }));
      });
    });
  } else {
    const barWidth = Math.min(
      34,
      groupWidth * 0.72 / Math.max(spec.series.length, 1)
    );
    spec.series.forEach((series, seriesIndex) => {
      series.values.forEach((value, index) => {
        const barHeight = plotHeight * value / maxValue;
        const groupStart = margin.left + groupWidth * index + groupWidth * 0.14;
        svg.appendChild(svgNode("rect", {
          x: groupStart + seriesIndex * barWidth,
          y: margin.top + plotHeight - barHeight,
          width: Math.max(barWidth - 3, 2),
          height: barHeight,
          rx: 4,
          fill: chartColors[seriesIndex]
        }));
      });
    });
  }

  const legend = document.createElement("div");
  legend.className = "chart-legend";
  spec.series.forEach((series, index) => {
    const item = document.createElement("span");
    const swatch = document.createElement("i");
    swatch.style.background = chartColors[index];
    item.append(swatch, document.createTextNode(series.name));
    legend.appendChild(item);
  });
  card.append(title, meta, svg, legend);
  return card;
}

async function renderResult(taskUrl) {
  const response = await fetch(taskUrl);
  const snapshot = await response.json();
  if (snapshot.status === "failed") throw new Error(snapshot.error || "分析失败");
  const result = snapshot.result;
  document.querySelector("#answerText").textContent = result.answer;
  charts.replaceChildren(...(result.charts || []).map(renderChart));
  document.querySelector("#evidence").replaceChildren(
    ...result.evidence.map(item => {
      const row = document.createElement("li");
      row.textContent = `${item.evidence_id} · ${item.summary}`;
      return row;
    })
  );
  document.querySelector("#snapshot").textContent =
    `数据快照：${result.data_snapshot_id} · Trace：${result.trace_ref}`;
  answer.hidden = false;
}

button.addEventListener("click", async () => {
  button.disabled = true;
  progress.hidden = false;
  answer.hidden = true;
  errorPanel.hidden = true;
  events.replaceChildren();
  try {
    const response = await fetch("/v1/tasks", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({question: question.value, channel: "web"})
    });
    if (!response.ok) throw new Error("请求未被服务接受");
    const accepted = await response.json();
    await new Promise((resolve, reject) => {
      const source = new EventSource(accepted.events_url);
      const types = Object.keys(labels);
      types.forEach(type => source.addEventListener(type, async event => {
        const payload = JSON.parse(event.data);
        addEvent(type, payload);
        if (type === "task.completed") {
          source.close();
          try { await renderResult(accepted.task_url); resolve(); }
          catch (error) { reject(error); }
        }
      }));
      source.addEventListener("task.failed", event => {
        source.close();
        reject(new Error(JSON.parse(event.data).data?.message || "分析失败"));
      });
      source.onerror = () => reject(new Error("事件连接中断"));
    });
  } catch (error) {
    errorPanel.textContent = error.message;
    errorPanel.hidden = false;
  } finally {
    button.disabled = false;
  }
});
