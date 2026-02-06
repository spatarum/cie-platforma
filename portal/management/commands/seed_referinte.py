from __future__ import annotations

from django.core.management.base import BaseCommand
from django.db import transaction

from portal.models import Chapter, Cluster, Criterion


class Command(BaseCommand):
    help = "Populează (sau completează) clusterele, capitolele și criteriile de bază (în limba română)."

    @transaction.atomic
    def handle(self, *args, **options):
        clusters = [
            # Culori per cluster (toate capitolele din același cluster vor folosi aceeași culoare în interfață)
            (1, "Valori fundamentale", "bi-shield-check", 10, "#4c1d95"),  # mov închis
            (2, "Piața internă", "bi-shop", 20, "#1e40af"),  # albastru
            (3, "Competitivitate și creștere incluzivă", "bi-graph-up-arrow", 30, "#b45309"),  # portocaliu/maro
            (4, "Agenda verde și conectivitate durabilă", "bi-leaf", 40, "#166534"),  # verde
            (5, "Resurse, agricultură și coeziune", "bi-geo", 50, "#0f766e"),  # teal
            (6, "Relații externe", "bi-globe", 60, "#0b3d91"),  # navy
        ]

        cluster_map: dict[int, Cluster] = {}
        for cod, denumire, pictograma, ordonare, culoare in clusters:
            obj, _created = Cluster.objects.get_or_create(
                cod=cod,
                defaults={"denumire": denumire, "pictograma": pictograma, "ordonare": ordonare, "culoare": culoare},
            )
            # Nu suprascriem modificările făcute manual de admin; completăm doar lipsurile.
            changed = False
            if obj.denumire != denumire:
                obj.denumire = denumire
                changed = True
            if (obj.pictograma or "") != pictograma:
                obj.pictograma = pictograma
                changed = True
            if (obj.culoare or "").lower() != culoare.lower():
                obj.culoare = culoare
                changed = True
            if changed:
                obj.save()
            cluster_map[cod] = obj

        criterii = [
            ("FID", "Funcționarea instituțiilor democratice", "bi-building", "#1e40af"),
            ("RAP", "Reforma administrației publice", "bi-gear", "#0f766e"),
            ("SD", "Statul de drept", "bi-shield-lock", "#7f1d1d"),
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
            # numar, cluster_cod, denumire, pictograma
            (1, 2, "Libera circulație a mărfurilor", "bi-box-seam"),
            (2, 2, "Libera circulație a lucrătorilor", "bi-people"),
            (3, 2, "Dreptul de stabilire și libera prestare a serviciilor", "bi-briefcase"),
            (4, 2, "Libera circulație a capitalului", "bi-currency-euro"),
            (5, 1, "Achiziții publice", "bi-bag-check"),
            (6, 2, "Dreptul societăților comerciale", "bi-building-gear"),
            (7, 2, "Dreptul de proprietate intelectuală", "bi-lightbulb"),
            (8, 2, "Politica în domeniul concurenței", "bi-trophy"),
            (9, 2, "Servicii financiare", "bi-bank"),
            (10, 3, "Societatea informațională și mass-media", "bi-broadcast"),
            (11, 5, "Agricultura și dezvoltarea rurală", "bi-tree"),
            (12, 5, "Siguranța alimentară, politici sanitare și fitosanitare", "bi-shield-plus"),
            (13, 5, "Pescuit", "bi-water"),
            (14, 4, "Politica de transport", "bi-truck"),
            (15, 4, "Energie", "bi-lightning-charge"),
            (16, 3, "Fiscalitate", "bi-receipt"),
            (17, 3, "Politica economică și monetară", "bi-cash-coin"),
            (18, 1, "Statistici", "bi-bar-chart"),
            (19, 3, "Politica socială și ocuparea forței de muncă", "bi-clipboard2-heart"),
            (20, 3, "Politica industrială și antreprenoriat", "bi-building-up"),
            (21, 4, "Rețele transeuropene", "bi-diagram-3"),
            (22, 5, "Politica regională și coordonarea instrumentelor structurale", "bi-map"),
            # Cap. 23: pictograma corectă ("bi-balance-scale" nu există în Bootstrap Icons)
            (23, 1, "Sistem judiciar și drepturi fundamentale", "bi-shield-check"),
            (24, 1, "Justiție, libertate și securitate", "bi-shield-lock"),
            (25, 3, "Știință și cercetare", "bi-mortarboard"),
            (26, 3, "Educație și cultură", "bi-book"),
            (27, 4, "Mediu și schimbări climatice", "bi-cloud-sun"),
            (28, 2, "Protecția consumatorului și a sănătății", "bi-heart-pulse"),
            (29, 3, "Uniunea vamală", "bi-boxes"),
            (30, 6, "Relații externe", "bi-people-fill"),
            (31, 6, "Politica externă, de securitate și de apărare", "bi-shield"),
            (32, 1, "Control financiar", "bi-clipboard-check"),
            (33, 5, "Dispoziții financiare și bugetare", "bi-piggy-bank"),
            # Cap. 34-35: tratate separat (în afara clusterelor)
            (34, None, "Instituții", "bi-building"),
            (35, None, "Alte aspecte", "bi-three-dots"),
        ]

        for numar, cluster_cod, denumire, pictograma in chapters:
            cluster_obj = cluster_map.get(cluster_cod) if cluster_cod else None

            # Culori per cluster (capitolele cu cluster preiau automat culoarea clusterului)
            culoare = cluster_obj.culoare if cluster_obj else ("#6b7280" if numar == 35 else "#475569")
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
            # Dacă icon-ul lipsește sau este unul cunoscut ca invalid, îl corectăm
            # (Cap. 23: am considerat bi-hammer nepotrivit; îl înlocuim automat)
            if numar == 23 and (not obj.pictograma or obj.pictograma.strip() in ("bi-balance-scale", "bi-hammer")):
                obj.pictograma = pictograma
                changed = True
            elif (not obj.pictograma) or (obj.pictograma.strip() == "bi-balance-scale"):
                obj.pictograma = pictograma
                changed = True

            # Forțăm culorile per cluster ca să fie uniforme în UI
            if cluster_obj is not None and obj.culoare != cluster_obj.culoare:
                obj.culoare = cluster_obj.culoare
                changed = True
            # Pentru capitolele fără cluster păstrăm culoarea implicită definită mai sus
            if cluster_obj is None and (not obj.culoare or obj.culoare == "#0b3d91"):
                obj.culoare = culoare
                changed = True

            if changed:
                obj.save()

        self.stdout.write(self.style.SUCCESS("Referințele au fost populate/actualizate cu succes."))
