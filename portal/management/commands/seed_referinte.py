from __future__ import annotations

from django.core.management.base import BaseCommand
from django.db import transaction

from portal.models import Chapter, Cluster, Criterion


class Command(BaseCommand):
    help = "Populează (sau completează) clusterele, capitolele și criteriile de bază (în limba română)."

    @transaction.atomic
    def handle(self, *args, **options):
        clusters = [
            (1, "Valori fundamentale", "bi-shield-check", 10),
            (2, "Piața internă", "bi-shop", 20),
            (3, "Competitivitate și creștere incluzivă", "bi-graph-up-arrow", 30),
            (4, "Agenda verde și conectivitate durabilă", "bi-leaf", 40),
            (5, "Resurse, agricultură și coeziune", "bi-geo", 50),
            (6, "Relații externe", "bi-globe", 60),
        ]

        cluster_map: dict[int, Cluster] = {}
        for cod, denumire, pictograma, ordonare in clusters:
            obj, _created = Cluster.objects.get_or_create(
                cod=cod,
                defaults={"denumire": denumire, "pictograma": pictograma, "ordonare": ordonare},
            )
            # Nu suprascriem modificările făcute manual de admin; completăm doar lipsurile.
            changed = False
            if not obj.denumire:
                obj.denumire = denumire
                changed = True
            if not obj.pictograma:
                obj.pictograma = pictograma
                changed = True
            if not obj.ordonare:
                obj.ordonare = ordonare
                changed = True
            if changed:
                obj.save()
            cluster_map[cod] = obj

        criterii = [
            ("FID", "Funcționarea instituțiilor democratice", "bi-building", "#1e40af"),
            ("RAP", "Reforma administrației publice", "bi-diagram-3", "#0f766e"),
            ("ECON", "Criterii economice", "bi-bar-chart-line", "#b45309"),
        ]
        for cod, denumire, pictograma, culoare in criterii:
            obj, _created = Criterion.objects.get_or_create(
                cod=cod,
                defaults={"denumire": denumire, "pictograma": pictograma, "culoare": culoare},
            )
            changed = False
            if not obj.denumire:
                obj.denumire = denumire
                changed = True
            if not obj.pictograma:
                obj.pictograma = pictograma
                changed = True
            if not obj.culoare or obj.culoare == "#0b3d91":
                obj.culoare = culoare
                changed = True
            if changed:
                obj.save()

        chapters = [
            (1, 2, "Libera circulație a mărfurilor", "bi-box-seam", "#2563eb"),
            (2, 2, "Libera circulație a lucrătorilor", "bi-people", "#0d9488"),
            (3, 2, "Dreptul de stabilire și libera prestare a serviciilor", "bi-briefcase", "#7c3aed"),
            (4, 2, "Libera circulație a capitalului", "bi-currency-euro", "#16a34a"),
            (5, 1, "Achiziții publice", "bi-bag-check", "#0284c7"),
            (6, 2, "Dreptul societăților comerciale", "bi-building-gear", "#334155"),
            (7, 2, "Dreptul de proprietate intelectuală", "bi-lightbulb", "#d97706"),
            (8, 2, "Politica în domeniul concurenței", "bi-trophy", "#ea580c"),
            (9, 2, "Servicii financiare", "bi-bank", "#059669"),
            (10, 3, "Societatea informațională și mass-media", "bi-broadcast", "#9333ea"),
            (11, 5, "Agricultura și dezvoltarea rurală", "bi-tree", "#15803d"),
            (12, 5, "Siguranța alimentară, politici sanitare și fitosanitare", "bi-shield-plus", "#b91c1c"),
            (13, 5, "Pescuit", "bi-water", "#0369a1"),
            (14, 4, "Politica de transport", "bi-truck", "#475569"),
            (15, 4, "Energie", "bi-lightning-charge", "#f59e0b"),
            (16, 3, "Fiscalitate", "bi-receipt", "#92400e"),
            (17, 3, "Politica economică și monetară", "bi-cash-coin", "#1e3a8a"),
            (18, 1, "Statistici", "bi-bar-chart", "#4f46e5"),
            (19, 3, "Politica socială și ocuparea forței de muncă", "bi-clipboard2-heart", "#be123c"),
            (20, 3, "Politica industrială și antreprenoriat", "bi-building-up", "#c2410c"),
            (21, 4, "Rețele transeuropene", "bi-diagram-3", "#0ea5e9"),
            (22, 5, "Politica regională și coordonarea instrumentelor structurale", "bi-map", "#6d28d9"),
            (23, 1, "Sistem judiciar și drepturi fundamentale", "bi-balance-scale", "#4c1d95"),
            (24, 1, "Justiție, libertate și securitate", "bi-shield-lock", "#7f1d1d"),
            (25, 3, "Știință și cercetare", "bi-mortarboard", "#0f766e"),
            (26, 3, "Educație și cultură", "bi-book", "#65a30d"),
            (27, 4, "Mediu și schimbări climatice", "bi-cloud-sun", "#166534"),
            (28, 2, "Protecția consumatorului și a sănătății", "bi-heart-pulse", "#db2777"),
            (29, 3, "Uniunea vamală", "bi-boxes", "#1f2937"),
            (30, 6, "Relații externe", "bi-people-fill", "#0b3d91"),
            (31, 6, "Politica externă, de securitate și de apărare", "bi-shield", "#111827"),
            (32, 1, "Control financiar", "bi-clipboard-check", "#1d4ed8"),
            (33, 5, "Dispoziții financiare și bugetare", "bi-piggy-bank", "#3f3f46"),
            (34, None, "Instituții", "bi-building", "#064e3b"),
            (35, None, "Alte aspecte", "bi-three-dots", "#6b7280"),
        ]

        for numar, cluster_cod, denumire, pictograma, culoare in chapters:
            cluster_obj = cluster_map.get(cluster_cod) if cluster_cod else None
            obj, _created = Chapter.objects.get_or_create(
                numar=numar,
                defaults={"denumire": denumire, "cluster": cluster_obj, "pictograma": pictograma, "culoare": culoare},
            )

            changed = False
            if not obj.denumire:
                obj.denumire = denumire
                changed = True
            # dacă capitolul e fără cluster și noi avem unul, completăm; dar nu suprascriem dacă admin a setat alt cluster
            if obj.cluster is None and cluster_obj is not None:
                obj.cluster = cluster_obj
                changed = True
            if not obj.pictograma:
                obj.pictograma = pictograma
                changed = True
            if not obj.culoare or obj.culoare == "#0b3d91":
                obj.culoare = culoare
                changed = True

            if changed:
                obj.save()

        self.stdout.write(self.style.SUCCESS("Referințele au fost populate/actualizate cu succes."))
