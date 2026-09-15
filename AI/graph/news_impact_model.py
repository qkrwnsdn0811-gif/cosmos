"""Two-layer news-conditioned GATv2 with separate direction/magnitude heads.

Each article is one graph. Only direct-mention nodes receive that article's
frozen text embedding and sentiment. Every node has a company embedding,
available static attributes, and direct/position/count metadata. GAT attention
uses one-hot relation type, past weight, sign and reverse-message flag as edge
features, so different relationship types have learnable effects. Two message
passes match the two-hop candidate policy. Residual paths preserve direct news.

Targets per node are (negative/neutral/positive) and bounded abs excess return,
for BOTH 1/3-session horizons. Attention is a model diagnostic, not evidence of
causality. Real prices/outcomes are never inputs to forward().
"""
import torch
from torch import nn
from torch_geometric.nn import GATv2Conv


class NewsGAT(nn.Module):
    def __init__(self, n_companies, static_dim, text_dim=384, edge_dim=11, hidden=64, dropout=0.15):
        super().__init__()
        self.config = dict(n_companies=n_companies, static_dim=static_dim, text_dim=text_dim,
                           edge_dim=edge_dim, hidden=hidden, dropout=dropout)
        self.company = nn.Embedding(n_companies, 16)
        self.text = nn.Sequential(nn.Linear(text_dim + 3, hidden), nn.GELU())
        self.node = nn.Linear(static_dim + 5 + 16, hidden)
        self.gat1 = GATv2Conv(hidden, hidden // 4, heads=4, edge_dim=edge_dim, dropout=dropout,
                             add_self_loops=True, fill_value=0.)
        self.gat2 = GATv2Conv(hidden, hidden // 4, heads=4, edge_dim=edge_dim, dropout=dropout,
                             add_self_loops=True, fill_value=0.)
        self.norm1, self.norm2 = nn.LayerNorm(hidden), nn.LayerNorm(hidden)
        self.dropout = nn.Dropout(dropout)
        self.direction = nn.Linear(hidden, 6)
        self.magnitude = nn.Linear(hidden, 2)

    def forward(self, text, sentiment, mentions, static, edge_index, edge_attr, attention=False):
        b, n, _ = mentions.shape
        ids = torch.arange(n, device=text.device)
        node = torch.cat([static.expand(b, -1, -1), mentions, self.company(ids).expand(b, -1, -1)], -1)
        news = self.text(torch.cat([text, sentiment], -1))
        h = self.node(node) + mentions[:, :, :1] * news[:, None, :]
        offsets = torch.arange(b, device=text.device) * n
        ei = (edge_index[:, None, :] + offsets[None, :, None]).reshape(2, -1)
        ea = edge_attr.repeat(b, 1)
        h = h.reshape(b * n, -1)
        h = self.norm1(h + self.dropout(torch.nn.functional.gelu(self.gat1(h, ei, ea))))
        if attention:
            out, weights = self.gat2(h, ei, ea, return_attention_weights=True)
        else:
            out = self.gat2(h, ei, ea)
        h = self.norm2(h + self.dropout(torch.nn.functional.gelu(out)))
        direction = self.direction(h).reshape(b, n, 2, 3)
        magnitude = self.magnitude(h).sigmoid().reshape(b, n, 2)
        return (direction, magnitude, weights) if attention else (direction, magnitude)
