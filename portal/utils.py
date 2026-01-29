from collections import defaultdict
from typing import Dict, List, Tuple

from .models import Chapter, Cluster


def group_chapters_by_cluster() -> List[Tuple[Cluster | None, List[Chapter]]]:
    """Returnează lista de (cluster, capitole) în ordinea clusterelor.

    Capitolele fără cluster (34, 35) sunt returnate la final sub cluster=None.
    """
    clusters = list(Cluster.objects.all().order_by("ordonare", "cod"))
    by_cluster: Dict[int, List[Chapter]] = defaultdict(list)
    no_cluster: List[Chapter] = []

    for ch in Chapter.objects.all().order_by("numar"):
        if ch.cluster_id:
            by_cluster[ch.cluster_id].append(ch)
        else:
            no_cluster.append(ch)

    result: List[Tuple[Cluster | None, List[Chapter]]] = []
    for cl in clusters:
        result.append((cl, by_cluster.get(cl.id, [])))
    if no_cluster:
        result.append((None, no_cluster))
    return result
